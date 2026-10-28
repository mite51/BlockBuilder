from __future__ import annotations
import threading
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from .providers import OpenAI, Meshy, SubmissionUnknown, RemoteFailure, sanitized
from .storage import write_json, safe_child, digest
from .imaging import VIEWS


class Signals(QObject):
    progress = Signal(str, str)
    finished = Signal(str)


class Worker(QRunnable):
    def __init__(self, asset, record, key):
        super().__init__()
        self.asset, self.record, self.key = asset, record, key
        self.stop = threading.Event()
        self.signals = Signals()

    def report(self, message):
        self.signals.progress.emit(self.record['id'],message)

    def run(self):
        asset, record = self.asset, self.record
        folder = asset.attempt_dir(record['stage'],record['id'])
        stage, settings = record['stage'],record['settings']
        api = None
        try:
            provider = 'openai' if stage in ('preview','turnaround') else 'meshy'
            api = OpenAI(self.key) if provider=='openai' else Meshy(self.key)
            remote = record.get('remote',{})
            if not remote.get('id'):
                if record['status']=='submission_unknown':
                    raise ValueError('This submission has an unknown outcome. Attach its provider ID or start a deliberate new attempt.')
                if self.stop.is_set():
                    raise InterruptedError('Paused before submission.')
                payload, endpoint = self.prepare(api)
                write_json(folder/'request.json',sanitized(payload))
                asset.update(record,status='submitting')
                self.report('Submitting to '+provider)
                response = api.submit(payload) if provider=='openai' else api.submit(endpoint,payload)
                task_id = response.get('id') if provider=='openai' else response.get('result')
                if not task_id:
                    raise SubmissionUnknown('Submission returned no task ID. Check the provider before creating another attempt.')
                remote = {'provider':provider, 'endpoint':endpoint,'id':task_id}
                asset.update(record,remote=remote,status='running')
                write_json(folder/'submission.json',sanitized(response))
            asset.update(record,status='running',error=None)
            failures = 0
            while True:
                if self.stop.is_set():
                    raise InterruptedError('Local monitoring paused. The remote job may continue.')
                try:
                    response = api.poll(remote['id']) if provider=='openai' else api.poll(remote['endpoint'],remote['id'])
                    failures = 0
                except RuntimeError:
                    failures += 1
                    if failures>=4:
                        raise
                    self.report('Connection interrupted; retrying status check…')
                    self.stop.wait(min(2**failures,20))
                    continue
                write_json(folder/'response.json',sanitized(response))
                status = response.get('status','').lower()
                if status in ('completed','succeeded'):
                    break
                if status in ('failed','canceled','cancelled','expired','incomplete'):
                    message = str(response.get('error') or response.get('task_error') or response.get('incomplete_details') or status)
                    raise RemoteFailure(message.replace(self.key,'[redacted]'))
                self.report(f"{status or 'running'} · {response.get('progress', '…')}" + ('%' if 'progress' in response else ''))
                self.stop.wait(4)
            asset.update(record,status='downloading')
            self.report('Saving generated files…')
            outputs = api.save_result(response,folder) if provider=='openai' else api.save_result(response,folder,self.stop,self.report)
            outputs.update({'request':'request.json','response':'response.json'})
            # A manually attached provider task may have no original local request.
            outputs = {k:v for k,v in outputs.items() if (folder/v).exists()}
            asset.succeed(record,outputs)
            self.report('Ready to review — select this result to continue.')
        except SubmissionUnknown as exc:
            asset.update(record,status='submission_unknown',error=str(exc))
            self.report(str(exc))
        except RemoteFailure as exc:
            asset.update(record,status='failed',error=str(exc))
            self.report('Provider failed: '+str(exc))
        except Exception as exc:
            status = 'interrupted' if record.get('remote',{}).get('id') or isinstance(exc,InterruptedError) else 'failed'
            message = str(exc).replace(self.key,'[redacted]') if self.key else str(exc)
            asset.update(record,status=status,error=message[:1500])
            self.report(message[:1500])
        finally:
            if api:
                api.close()
            self.key = ''
            self.signals.finished.emit(record['id'])

    def prepare(self, api):
        asset, record = self.asset,self.record
        settings = record['settings']
        stage = record['stage']
        parent = lambda s: asset.get(s,record['inputs'][s])
        if stage in ('preview','turnaround'):
            desc = parent('description')['spec']
            for ref in desc.get('references',[]):
                if ref.get('working_sha256') and digest(safe_child(asset.path,ref['path']))!=ref['working_sha256']:
                    raise ValueError('A source image changed on disk. Add it again as a new reference.')
            images = [safe_child(asset.path,r['path']) for r in desc.get('references',[])]
            if stage=='turnaround':
                images.insert(0,asset.file(parent('preview'),'image'))
            if record['spec'].get('edit_attempt'):
                previous = asset.get(stage,record['spec']['edit_attempt'])
                images.insert(0,asset.file(previous,'image'))
            return api.payload(record['spec']['prompt'],images,settings),'responses'
        images = [asset.file(parent('split'),name) for name in VIEWS]
        if stage=='mesh':
            return api.mesh_payload(images,settings),'multi-image-to-3d'
        mesh = parent('mesh')
        return api.texture_payload(asset.file(mesh,'model'),images,record['spec'].get('prompt',''),settings,
                                   mesh.get('remote',{}).get('id')),'retexture'


class JobManager(QObject):
    progress = Signal(str,str)
    finished = Signal(str)

    def __init__(self, credentials, concurrency=2, parent=None):
        super().__init__(parent)
        self.credentials = credentials
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(concurrency)
        self.workers = {}

    def start(self, asset, record):
        if record['id'] in self.workers:
            raise ValueError('This attempt is already running.')
        provider = 'openai' if record['stage'] in ('preview','turnaround') else 'meshy'
        key = self.credentials.get(provider)
        if not key:
            raise ValueError(f'Add the {provider} API key in Settings.')
        worker = Worker(asset,record,key)
        worker.signals.progress.connect(self.progress.emit)
        worker.signals.finished.connect(self._done)
        self.workers[record['id']] = worker
        self.pool.start(worker)

    def _done(self, attempt_id):
        self.workers.pop(attempt_id,None)
        self.finished.emit(attempt_id)

    def pause(self, attempt_id):
        if attempt_id in self.workers:
            self.workers[attempt_id].stop.set()

    def pause_all(self):
        for worker in self.workers.values():
            worker.stop.set()
