from __future__ import annotations
import base64
import hashlib
import mimetypes
from pathlib import Path
import httpx
from .storage import atomic_bytes


class SubmissionUnknown(RuntimeError):
    pass


class RemoteFailure(RuntimeError):
    pass


def data_uri(path, model=False):
    path = Path(path)
    limit = 100 if model else 20
    if path.stat().st_size > limit * 1024 * 1024:
        raise ValueError(f'{path.name} exceeds the {limit} MB upload limit.')
    mime = 'application/octet-stream' if model else (mimetypes.guess_type(path.name)[0] or 'image/png')
    return f'data:{mime};base64,' + base64.b64encode(path.read_bytes()).decode('ascii')


def sanitized(value):
    if isinstance(value, dict):
        return {k:sanitized(v) for k,v in value.items() if k.lower() not in {'authorization','api_key','api-key'}}
    if isinstance(value, list):
        return [sanitized(v) for v in value]
    if isinstance(value, str) and (value.startswith('data:') or len(value) > 100000):
        return {'encoded_artifact_sha256':hashlib.sha256(value.encode()).hexdigest(), 'length':len(value)}
    return value


class API:
    def __init__(self, key, base, transport=None):
        if not key:
            raise ValueError('Configure MESHY_API_KEY or the saved BlockBuilder/meshy credential before generating.')
        self.key = key
        self.base = base
        self.client = httpx.Client(timeout=httpx.Timeout(120, connect=20), transport=transport)

    def close(self):
        self.client.close()

    def request(self, method, path, body=None):
        try:
            response = self.client.request(method,self.base+path,json=body,
                                           headers={'Authorization':'Bearer '+self.key})
        except httpx.TransportError as exc:
            if method == 'POST':
                raise SubmissionUnknown('The connection ended during submission. The job may exist remotely. Do not automatically resubmit.') from exc
            raise RuntimeError('Network error while checking the provider. Resume this attempt when connected.') from exc
        if response.status_code >= 400:
            try:
                message = response.json().get('error', response.json().get('message',''))
                if isinstance(message,dict):
                    message = message.get('message', str(message))
            except ValueError:
                message = response.text[:500]
            message = str(message).replace(self.key,'[redacted]')[:1000]
            if method == 'POST' and (response.status_code >= 500 or response.status_code in (408,)):
                raise SubmissionUnknown(f'Provider returned HTTP {response.status_code} during submission. Check the provider before rerunning. {message}')
            raise RuntimeError(f'HTTP {response.status_code}: {message or response.reason_phrase}')
        try:
            return response.json()
        except ValueError as exc:
            if method == 'POST':
                raise SubmissionUnknown('Submission response could not be read; check the provider before rerunning.') from exc
            raise RuntimeError('Provider returned an unreadable response.') from exc


class Meshy(API):
    def __init__(self, key, transport=None):
        super().__init__(key,'https://api.meshy.ai/openapi/v1',transport)

    @staticmethod
    def mesh_payload(images, settings):
        if len(images)!=4:
            raise ValueError('Select all four views first.')
        if settings['geometry_resolution']=='2k' and settings['mesh_model'] not in ('meshy-7.1','latest','meshy-7'):
            raise ValueError('Ultra geometry requires meshy-7.1. Change the model or choose standard detail.')
        return {'image_urls':[data_uri(p) for p in images], 'ai_model':settings['mesh_model'],
                'geometry_resolution':settings['geometry_resolution'], 'should_texture':False,
                'should_remesh':True,'topology':'triangle','target_polycount':int(settings['polycount']),
                'target_formats':['glb']}

    @staticmethod
    def texture_payload(model, images, prompt, settings, remote_mesh_id=None):
        body = {'ai_model':settings['texture_model'], 'enable_pbr':settings['enable_pbr'],
                'texture_resolution':settings['texture_resolution'], 'target_formats':['glb']}
        if settings['texture_model']=='meshy-6-lite' and settings['texture_resolution']!='2k':
            raise ValueError('meshy-6-lite supports 2k textures. Choose 2k or another texture model.')
        # Submit the exact accepted file so even locally imported meshes work.
        if Path(model).stat().st_size > 100*1024*1024 and remote_mesh_id:
            body['input_task_id'] = remote_mesh_id
        else:
            body['model_url'] = data_uri(model,model=True)
        from .geometry import GLB
        glb = GLB(model)
        body['enable_original_uv'] = all('TEXCOORD_0' in p.get('attributes',{}) for m in glb.doc.get('meshes',[]) for p in m['primitives'])
        mode = settings['texture_mode']
        if mode == 'multiview':
            if settings['texture_model'] != 'meshy-7':
                raise ValueError('Meshy multiview retexturing requires the explicit meshy-7 texture model.')
            body['multiview_image_urls'] = [data_uri(p) for p in images]
        elif mode == 'image':
            body['image_style_url'] = data_uri(images[0])
        elif mode == 'text':
            if not prompt.strip() or len(prompt)>800:
                raise ValueError('Texture text must contain 1–800 characters.')
            body['text_style_prompt'] = prompt
        else:
            raise ValueError('Unknown texture mode.')
        return body

    def submit(self, endpoint, payload):
        return self.request('POST','/'+endpoint,payload)

    def poll(self, endpoint, task_id):
        return self.request('GET','/'+endpoint+'/'+task_id)

    def test(self, settings=None):
        value = self.request('GET','/balance')
        return f"Connected. Available credits: {value.get('balance', 'unknown')}."

    def save_result(self, response, folder, stop, progress):
        folder = Path(folder)
        outputs = {}
        files = []
        model = response.get('model_urls',{}).get('glb')
        if not model:
            raise RemoteFailure('Meshy finished but returned no GLB.')
        files.append(('model','model.glb',model))
        if response.get('thumbnail_url'):
            files.append(('thumbnail','thumbnail.png',response['thumbnail_url']))
        for i, texture in enumerate(response.get('texture_urls',[])):
            for kind, url in texture.items():
                if isinstance(url,str) and url.startswith('https://'):
                    safe_kind = ''.join(c for c in kind if c.isalnum() or c=='_')
                    suffix = Path(httpx.URL(url).path).suffix.lower()
                    if suffix not in ('.png','.jpg','.jpeg','.webp'):
                        suffix = '.png'
                    files.append((f'texture_{i}_{safe_kind}',f'textures/{i}_{safe_kind}{suffix}',url))
        for key, name, url in files:
            progress('Downloading '+name)
            destination = folder/name
            destination.parent.mkdir(parents=True,exist_ok=True)
            tmp = destination.with_suffix(destination.suffix+'.part')
            # Download clients never inherit API Authorization headers.
            with httpx.stream('GET',url,follow_redirects=True,timeout=120) as response_stream:
                response_stream.raise_for_status()
                with tmp.open('wb') as out:
                    total = 0
                    for chunk in response_stream.iter_bytes():
                        if stop.is_set():
                            raise InterruptedError('Download paused; resume to fetch it again.')
                        total += len(chunk)
                        if total > 1024*1024*1024:
                            raise ValueError('Provider download exceeds the 1 GB limit.')
                        out.write(chunk)
            tmp.replace(destination)
            outputs[key] = name
        from .geometry import inspect
        inspect(folder/'model.glb')
        return outputs
