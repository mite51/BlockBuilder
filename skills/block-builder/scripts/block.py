"""Finite file/API operations for the conversational Block Builder skill.

Image generation and decisions belong to the host conversation, not this CLI.
Run --help or a subcommand's --help. Commands emit JSON and never print keys.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import threading

from core.storage import Asset, Library, STAGES, atomic_bytes, digest, read_json, safe_child, write_json

SKILL = Path(__file__).resolve().parent.parent
MESH_DEFAULTS = {'mesh_model':'meshy-7.1','geometry_resolution':'standard','polycount':30000,
                 'texture_model':'meshy-7','texture_mode':'multiview',
                 'texture_resolution':'2k','enable_pbr':True}


@contextmanager
def library_lock(root):
    """Use the desktop's lock when Qt exists; otherwise an exclusive lock file."""
    root=Path(root).resolve(); root.mkdir(parents=True,exist_ok=True)
    path=root/'.block_builder.lock'
    try:
        from PySide6.QtCore import QLockFile
    except ImportError:
        try:
            stream=path.open('x',encoding='utf-8')
        except FileExistsError:
            raise ValueError('Library is locked. Close its viewer/writer first; inspect a stale lock before removing it.')
        with stream:
            stream.write(f'{os.getpid()}\n{Path(sys.executable).stem}\n{socket.gethostname()}\n')
        try: yield
        finally: path.unlink(missing_ok=True)
    else:
        lock=QLockFile(str(path)); lock.setStaleLockTime(0)
        if not lock.tryLock(1):
            raise ValueError('Library is open in another process. Close the desktop viewer before changing this library from the skill.')
        try: yield
        finally: lock.unlock()


def report(asset, record):
    result={k:record[k] for k in ('id','stage','status','inputs')}
    result.update(asset=str(asset.path), compatible=asset.compatible(record),
                  selected=asset.data['selections'].get(record['stage'])==record['id'],
                  outputs={key:str(asset.file(record,key)) for key in record['outputs']},
                  remote=record.get('remote',{}),error=record.get('error'))
    if record['status']=='submitting' and not record.get('remote',{}).get('id'):
        result['needs_reconciliation']=True
    return result


def finish_local(asset, record, operation):
    try:
        outputs=operation(); asset.succeed(record,outputs)
    except Exception as exc:
        asset.update(record,status='failed',error=str(exc)); raise
    return report(asset,record)


def describe(asset, args):
    materials=read_json(SKILL/'assets/materials.json')
    text=Path(args.text_file).read_text(encoding='utf-8').strip() if args.text_file else materials[args.preset]
    if not text: raise ValueError('Description is empty.')
    refs=[asset.add_source(path) for path in args.reference]
    rec=asset.begin('description',{'description':text,'references':refs})
    folder=asset.attempt_dir(rec['stage'],rec['id'])
    write_json(folder/'description.json',rec['spec']); asset.succeed(rec,{'description':'description.json'})
    return report(asset,rec)


def prepare_image(asset,args):
    desc=asset.selected('description')['spec']
    prompt=Path(args.prompt_file).read_text(encoding='utf-8') if args.prompt_file else (SKILL/'assets'/f'{args.stage}.txt').read_text(encoding='utf-8').replace('{{description}}',desc['description'])
    refs=[]
    if args.edit_attempt:
        old=asset.get(args.stage,args.edit_attempt)
        if old['status']!='succeeded': raise ValueError('Choose a successful image attempt to edit.')
        refs.append(('edit_target',asset.file(old,'image')))
    if args.stage=='turnaround': refs.append(('accepted_preview',asset.file(asset.selected('preview'),'image')))
    for ref in desc.get('references',[]):
        path=safe_child(asset.path,ref['path'])
        if ref.get('working_sha256') and digest(path)!=ref['working_sha256']:
            raise ValueError('A reference changed on disk; add it as a new description revision.')
        refs.append(('description_reference',path))
    refs.extend(('additional_reference',Path(p)) for p in args.reference)
    for _,path in refs:
        if not path.is_file(): raise ValueError(f'Missing reference: {path}')
    rec=asset.begin(args.stage,{'prompt':prompt,'template_version':1,'tool':'built-in image_gen',
                                'edit_attempt':args.edit_attempt,'references':[]})
    folder=asset.attempt_dir(args.stage,rec['id']); atomic_bytes(folder/'prompt.txt',prompt.encode('utf-8'))
    saved=[]
    for index,(role,source) in enumerate(refs):
        target=folder/'input_refs'/f'{index+1:02d}_{role}{source.suffix.lower()}'
        target.parent.mkdir(exist_ok=True); shutil.copy2(source,target)
        saved.append({'role':role,'path':target.relative_to(folder).as_posix(),'sha256':digest(target)})
    rec['spec']['references']=saved; asset.update(rec)
    result=report(asset,rec)
    result.update(prompt_file=str(folder/'prompt.txt'),
                  reference_images=[{'role':r['role'],'path':str(folder/r['path'])} for r in saved])
    return result


def record_image(asset,args):
    from PIL import Image,ImageOps
    rec=asset.get(args.stage,args.attempt)
    if rec['status'] not in ('pending','interrupted'):
        raise ValueError('This attempt is already finished. Prepare a new attempt for another generation.')
    source=Path(args.image)
    with Image.open(source) as im: im.verify()
    folder=asset.attempt_dir(args.stage,rec['id'])
    original='generated_original'+source.suffix.lower()
    def save():
        shutil.copy2(source,folder/original)
        with Image.open(source) as im: ImageOps.exif_transpose(im).save(folder/'image.png')
        meta=read_json(args.metadata) if args.metadata else {}
        # Metadata should contain only actual tool-returned IDs/model information.
        from core.meshy import sanitized
        write_json(folder/'generation.json',{'tool':'built-in image_gen','source_filename':source.name,
                                           'source_sha256':digest(source),'returned_metadata':sanitized(meta)})
        outputs={'image':'image.png','original':original,'generation':'generation.json','prompt':'prompt.txt'}
        for i,ref in enumerate(rec['spec'].get('references',[])):
            outputs[f'reference_{i+1}']=ref['path']
        return outputs
    return finish_local(asset,rec,save)


def split(asset,args):
    from core.imaging import split_sheet
    spec=read_json(args.spec_file) if args.spec_file else {'padding':args.padding,'output_size':args.size}
    rec=asset.begin('split',spec); folder=asset.attempt_dir('split',rec['id'])
    source=asset.file(asset.selected('turnaround'),'image')
    return finish_local(asset,rec,lambda:split_sheet(source,folder,**spec))


def meshy_submit(asset,args):
    from core.credentials import Credentials
    from core.meshy import Meshy, SubmissionUnknown, sanitized
    from core.imaging import VIEWS
    key=Credentials().get('meshy')
    if not key: raise ValueError('Configure MESHY_API_KEY locally or save a Meshy key in the existing Block Builder Settings. Do not paste keys in chat.')
    settings=dict(MESH_DEFAULTS)
    if args.settings:
        overrides=read_json(args.settings)
        unknown=set(overrides)-set(MESH_DEFAULTS)
        if unknown: raise ValueError('Unsupported Meshy settings: '+', '.join(sorted(unknown)))
        settings.update(overrides)
    rec=asset.begin(args.command,{'prompt':Path(args.prompt_file).read_text(encoding='utf-8') if args.prompt_file else ''},settings)
    folder=asset.attempt_dir(rec['stage'],rec['id'])
    api=Meshy(key)
    try:
        crops=asset.selected('split'); images=[asset.file(crops,name) for name in VIEWS]
        if args.command=='mesh':
            endpoint='multi-image-to-3d'; body=api.mesh_payload(images,settings)
        else:
            mesh=asset.selected('mesh'); endpoint='retexture'
            body=api.texture_payload(asset.file(mesh,'model'),images,rec['spec']['prompt'],settings,mesh.get('remote',{}).get('id'))
        write_json(folder/'request.json',sanitized(body))
        asset.update(rec,status='submitting')
        response=api.submit(endpoint,body)
        task_id=response.get('result') if isinstance(response,dict) else None
        if not isinstance(task_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',task_id):
            raise SubmissionUnknown('No usable task ID returned. Reconcile this attempt before making another request.')
        asset.update(rec,remote={'provider':'meshy','endpoint':endpoint,'id':task_id},status='running')
        write_json(folder/'submission.json',sanitized(response))
    except SubmissionUnknown as exc:
        asset.update(rec,status='submission_unknown',error=str(exc).replace(key,'[redacted]'))
    except Exception as exc:
        # A received ID means the remote task can be resumed, even if local saving failed.
        status='interrupted' if rec.get('remote',{}).get('id') else 'failed'
        asset.update(rec,status=status,error=str(exc).replace(key,'[redacted]'))
    finally: api.close()
    return report(asset,rec)


def poll(asset,args):
    from core.credentials import Credentials
    from core.meshy import Meshy,sanitized
    rec=asset.get(args.stage,args.attempt)
    if rec['status']=='succeeded': return report(asset,rec)
    task_id=rec.get('remote',{}).get('id')
    if not task_id: raise ValueError('No remote task ID. Reconcile the submission; polling never creates a replacement task.')
    api=Meshy(Credentials().get('meshy')); folder=asset.attempt_dir(args.stage,rec['id'])
    try:
        response=api.poll(rec['remote']['endpoint'],task_id)
        write_json(folder/'response.json',sanitized(response))
        status=response.get('status','').lower()
        if status=='succeeded':
            asset.update(rec,status='downloading')
            outputs=api.save_result(response,folder,threading.Event(),lambda message:None)
            outputs['response']='response.json'
            if (folder/'request.json').exists(): outputs['request']='request.json'
            asset.succeed(rec,outputs)
        elif status in ('failed','canceled','cancelled','expired'):
            asset.update(rec,status='failed',error=str(response.get('task_error') or status).replace(api.key,'[redacted]'))
        else: asset.update(rec,status='running',progress=response.get('progress'))
    except Exception as exc:
        asset.update(rec,status='interrupted',error=str(exc).replace(api.key,'[redacted]'))
    finally: api.close()
    return report(asset,rec)


def bounds(asset,args):
    from core.geometry import suggest_bounds,normalize_glb
    source=asset.file(asset.selected('texture'),'model')
    spec=read_json(args.spec_file) if args.spec_file else suggest_bounds(source)
    rec=asset.begin('bounds',spec); folder=asset.attempt_dir('bounds',rec['id'])
    def save():
        metadata=normalize_glb(source,folder/'model.glb',spec)
        write_json(folder/'bounds.json',metadata)
        return {'model':'model.glb','bounds':'bounds.json'}
    return finish_local(asset,rec,save)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    subs=parser.add_subparsers(dest='command',required=True)
    new=subs.add_parser('new'); new.add_argument('--root',required=True); new.add_argument('--name',required=True)
    listing=subs.add_parser('list'); listing.add_argument('--root',required=True)
    subs.add_parser('materials')
    subs.add_parser('credentials')
    viewer=subs.add_parser('viewer'); viewer.add_argument('--repo',required=True); viewer.add_argument('--root',required=True)
    commands={}
    for name in ('status','describe','prepare-image','record-image','select','split','mesh','texture','poll','attach-job','fail','bounds','complete'):
        sub=subs.add_parser(name); sub.add_argument('--asset',required=True); commands[name]=sub
    p=commands['describe']; group=p.add_mutually_exclusive_group(required=True)
    group.add_argument('--text-file'); group.add_argument('--preset'); p.add_argument('--reference',action='append',default=[])
    p=commands['prepare-image']; p.add_argument('--stage',choices=['preview','turnaround'],required=True)
    p.add_argument('--prompt-file'); p.add_argument('--reference',action='append',default=[]); p.add_argument('--edit-attempt')
    p=commands['record-image']; p.add_argument('--stage',choices=['preview','turnaround'],required=True)
    p.add_argument('--attempt',required=True); p.add_argument('--image',required=True); p.add_argument('--metadata')
    p=commands['select']; p.add_argument('--stage',choices=STAGES,required=True); p.add_argument('--attempt',required=True); p.add_argument('--restore',action='store_true')
    p=commands['split']; p.add_argument('--spec-file'); p.add_argument('--padding',type=int,default=16); p.add_argument('--size',type=int,default=1024)
    for name in ('mesh','texture'):
        commands[name].add_argument('--settings'); commands[name].add_argument('--prompt-file')
    for name in ('poll','attach-job'):
        commands[name].add_argument('--stage',choices=['mesh','texture'],required=True); commands[name].add_argument('--attempt',required=True)
    commands['attach-job'].add_argument('--job-id',required=True)
    p=commands['fail']; p.add_argument('--stage',choices=STAGES,required=True); p.add_argument('--attempt',required=True); p.add_argument('--reason',required=True)
    commands['bounds'].add_argument('--spec-file')
    commands['complete'].add_argument('--thumbnail')
    args=parser.parse_args(argv)
    if args.command=='materials': return read_json(SKILL/'assets/materials.json')
    if args.command=='credentials':
        from core.credentials import Credentials
        return {'meshy_configured':bool(Credentials().get('meshy')),'image_provider':'host built-in image_gen; no OpenAI API key needed'}
    if args.command=='viewer':
        repo=Path(args.repo).resolve()
        if not (repo/'block_builder/app.py').is_file(): raise ValueError('Point --repo at an existing Block Builder checkout.')
        executable=repo/'.venv/Scripts/pythonw.exe'
        if not executable.exists():
            windowless=Path(sys.executable).with_name('pythonw.exe')
            executable=windowless if windowless.exists() else Path(sys.executable)
        # An interactive viewer only; no job is submitted by opening the app.
        process=subprocess.Popen([str(executable),'-m','block_builder','--library',str(Path(args.root).resolve())],cwd=repo)
        return {'viewer_pid':process.pid,'library':str(Path(args.root).resolve()),'instruction':'Use the app for 3D review/bounds. Request image generation in chat. Close the viewer before skill writes.'}
    if args.command=='list':
        library=Library(args.root); assets=library.scan(); results=[]
        for asset in assets:
            result={'name':asset.data['name'],'asset':str(asset.path),'completed_revision':asset.data['completed_revision']}
            if asset.data['completed_revision']:
                folder=safe_child(asset.path,'completed/'+asset.data['completed_revision'])
                result.update(model=str(folder/'block.glb'),thumbnail=str(folder/'thumbnail.png'))
            results.append(result)
        return {'assets':results,'errors':library.errors}
    if args.command=='new':
        with library_lock(args.root):
            asset=Asset.create(args.root,args.name); return {'asset':str(asset.path),'name':asset.data['name']}
    path=Path(args.asset).resolve()
    if args.command=='status':
        asset=Asset(path)
        return {'asset':str(path),'name':asset.data['name'],'completed_revision':asset.data['completed_revision'],
                'attempts':[report(asset,r) for stage in STAGES for r in asset.attempts(stage)]}
    with library_lock(path.parent):
        asset=Asset(path)
        if args.command=='describe': return describe(asset,args)
        if args.command=='prepare-image': return prepare_image(asset,args)
        if args.command=='record-image': return record_image(asset,args)
        if args.command=='split': return split(asset,args)
        if args.command in ('mesh','texture'): return meshy_submit(asset,args)
        if args.command=='poll': return poll(asset,args)
        if args.command=='bounds': return bounds(asset,args)
        if args.command=='complete': return {'completed_folder':str(asset.complete(args.thumbnail))}
        rec=asset.get(args.stage,args.attempt)
        if args.command=='select': asset.choose(rec,restore=args.restore)
        elif args.command=='attach-job':
            if not re.fullmatch(r'[A-Za-z0-9_-]+',args.job_id): raise ValueError('Invalid task ID.')
            if rec['status']=='succeeded' or rec.get('remote',{}).get('id'):
                raise ValueError('This attempt already has a task or successful result; resume it rather than replacing its identity.')
            endpoint='multi-image-to-3d' if args.stage=='mesh' else 'retexture'
            asset.update(rec,status='interrupted',remote={'provider':'meshy','endpoint':endpoint,'id':args.job_id})
        elif args.command=='fail':
            if rec['status']=='succeeded': raise ValueError('Completed attempt output is immutable; create another attempt.')
            asset.update(rec,status='failed',error=args.reason)
        return report(asset,rec)


if __name__=='__main__':
    try:
        result=main()
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except Exception as exc:
        print(json.dumps({'error':str(exc)},ensure_ascii=False),file=sys.stderr)
        sys.exit(1)
