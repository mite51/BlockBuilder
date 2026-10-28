"""Offline behavioral tests for the installed skill's finite operations helper."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import uuid

import pytest
from PIL import Image

# Works both in the source repo (tests/skill) and in the isolated authoring area.
HERE=Path(__file__).resolve().parent
SKILL=(HERE.parents[1]/'skills'/'block-builder') if HERE.name=='skill' else HERE/'block-builder'
sys.path.insert(0,str(SKILL/'scripts'))
spec=importlib.util.spec_from_file_location('skill_block_cli',SKILL/'scripts/block.py')
cli=importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
from core.storage import Asset,read_json,write_json,digest
from core.geometry import create_example_glb,inspect,GLB
from core.meshy import SubmissionUnknown


@pytest.fixture
def work():
    root=(HERE.parents[1]/'output'/'skill_tests') if HERE.name=='skill' else HERE/'test_output'
    path=root/uuid.uuid4().hex
    path.mkdir(parents=True)
    return path


def command(operation,asset=None,**kwargs):
    argv=[operation]
    if asset: argv+=['--asset',str(asset)]
    for key,value in kwargs.items():
        option='--'+key.replace('_','-')
        if value is True: argv.append(option)
        elif isinstance(value,list):
            for item in value: argv.extend([option,str(item)])
        elif value is not None: argv.extend([option,str(value)])
    return cli.main(argv)


def new_asset(work):
    asset=command('new',root=work/'cube_asset',name='Coarse Dirt')['asset']
    desc=command('describe',asset,preset='Coarse Dirt')
    command('select',asset,stage='description',attempt=desc['id'])
    return asset


def image_attempt(work,asset,stage,color='brown'):
    prepared=command('prepare-image',asset,stage=stage)
    output=work/(uuid.uuid4().hex+'.png')
    Image.new('RGB',(101,99),color).save(output)
    result=command('record-image',asset,stage=stage,attempt=prepared['id'],image=output)
    return prepared,output,result


def accepted_split(work):
    asset=new_asset(work)
    for stage in ('preview','turnaround'):
        _,_,rec=image_attempt(work,asset,stage)
        command('select',asset,stage=stage,attempt=rec['id'])
    rec=command('split',asset,size=256)
    command('select',asset,stage='split',attempt=rec['id'])
    return asset


def test_native_image_record_keeps_original_and_requires_selection(work):
    asset=new_asset(work)
    prepared,original,result=image_attempt(work,asset,'preview')
    assert original.is_file()
    assert digest(original)==digest(result['outputs']['original'])
    assert result['selected'] is False
    assert Path(prepared['prompt_file']).is_file()
    with pytest.raises(ValueError): command('prepare-image',asset,stage='turnaround')
    with pytest.raises(ValueError): command('record-image',asset,stage='preview',attempt=result['id'],image=original)


def test_turnaround_snapshots_accepted_preview_for_native_tool(work):
    asset=new_asset(work)
    _,_,preview=image_attempt(work,asset,'preview')
    command('select',asset,stage='preview',attempt=preview['id'])
    prepared=command('prepare-image',asset,stage='turnaround')
    ref=prepared['reference_images'][0]
    assert ref['role']=='accepted_preview'
    assert digest(ref['path'])==digest(preview['outputs']['image'])
    assert Path(ref['path']).is_relative_to(Path(asset))


def test_rerun_preserves_previous_generation_and_stales_descendants(work):
    asset=accepted_split(work)
    _,_,new=image_attempt(work,asset,'preview','blue')
    previous=Asset(asset).selected('turnaround')
    command('select',asset,stage='preview',attempt=new['id'])
    assert Asset(asset).selected('split',False) is None
    assert len(Asset(asset).attempts('preview'))==2
    with pytest.raises(ValueError): command('select',asset,stage='turnaround',attempt=previous['id'])
    command('select',asset,stage='turnaround',attempt=previous['id'],restore=True)
    assert Asset(asset).selected('split')


def test_source_snapshot_survives_original_reference_change(work):
    original=work/'ref.png'; Image.new('RGB',(10,10),'red').save(original)
    asset=command('new',root=work/'cube_asset',name='Rooted dirt')['asset']
    rec=command('describe',asset,preset='Rooted Dirt',reference=[original])
    command('select',asset,stage='description',attempt=rec['id'])
    Image.new('RGB',(10,10),'blue').save(original)
    prepared=command('prepare-image',asset,stage='preview')
    with Image.open(prepared['reference_images'][0]['path']) as image:
        assert image.convert('RGB').getpixel((0,0))==(255,0,0)


def test_meshy_unknown_submission_not_retried(work,monkeypatch):
    asset=accepted_split(work); calls=[]
    monkeypatch.setattr('core.credentials.Credentials.get',lambda self,provider:'test-key')
    def submit(self,endpoint,payload):
        calls.append(payload); raise SubmissionUnknown('Outcome unknown')
    monkeypatch.setattr('core.meshy.Meshy.submit',submit)
    result=command('mesh',asset)
    assert result['status']=='submission_unknown' and len(calls)==1
    with pytest.raises(ValueError): command('poll',asset,stage='mesh',attempt=result['id'])
    assert len(calls)==1
    command('attach-job',asset,stage='mesh',attempt=result['id'],job_id='actual-task-id')
    with pytest.raises(ValueError): command('attach-job',asset,stage='mesh',attempt=result['id'],job_id='replacement-id')


@pytest.mark.parametrize('response',[None,[],{'result':123},{'result':''}])
def test_unusable_submission_id_is_ambiguous(work,monkeypatch,response):
    asset=accepted_split(work)
    monkeypatch.setattr('core.credentials.Credentials.get',lambda self,provider:'test-key')
    monkeypatch.setattr('core.meshy.Meshy.submit',lambda *args:response)
    result=command('mesh',asset)
    assert result['status']=='submission_unknown'
    assert not result['remote'].get('id')


def test_poll_uses_saved_id_and_never_submits(work,monkeypatch):
    asset=accepted_split(work)
    monkeypatch.setattr('core.credentials.Credentials.get',lambda self,provider:'test-key')
    monkeypatch.setattr('core.meshy.Meshy.submit',lambda self,endpoint,body:{'result':'mesh-123'})
    result=command('mesh',asset)
    calls=[]
    def poll(self,endpoint,task_id):
        calls.append((endpoint,task_id)); return {'status':'SUCCEEDED','model_urls':{'glb':'https://example.test/model.glb'}}
    def save(self,response,folder,stop,progress):
        create_example_glb(folder/'model.glb',protrusion=True); return {'model':'model.glb'}
    monkeypatch.setattr('core.meshy.Meshy.poll',poll); monkeypatch.setattr('core.meshy.Meshy.save_result',save)
    monkeypatch.setattr('core.meshy.Meshy.submit',lambda *args:pytest.fail('Poll must not submit'))
    done=command('poll',asset,stage='mesh',attempt=result['id'])
    assert done['status']=='succeeded' and done['selected'] is False
    assert calls==[('multi-image-to-3d','mesh-123')]
    command('poll',asset,stage='mesh',attempt=result['id'])
    assert len(calls)==1


def test_complete_chain_and_bounds_correction_preserve_overhang(work,monkeypatch):
    asset=accepted_split(work)
    monkeypatch.setattr('core.credentials.Credentials.get',lambda self,provider:'test-key')
    requests=[]
    def submit(self,endpoint,payload):
        requests.append((endpoint,payload)); return {'result':'id-'+endpoint}
    def save(self,response,folder,stop,progress):
        create_example_glb(folder/'model.glb',protrusion=True); return {'model':'model.glb'}
    monkeypatch.setattr('core.meshy.Meshy.submit',submit)
    monkeypatch.setattr('core.meshy.Meshy.poll',lambda *args:{'status':'SUCCEEDED'})
    monkeypatch.setattr('core.meshy.Meshy.save_result',save)
    for stage in ('mesh','texture'):
        submitted=command(stage,asset)
        done=command('poll',asset,stage=stage,attempt=submitted['id'])
        command('select',asset,stage=stage,attempt=done['id'])
    assert requests[0][1]['should_texture'] is False
    assert len(requests[1][1]['multiview_image_urls'])==4
    assert requests[1][1]['ai_model']=='meshy-7'
    guess=command('bounds',asset)
    assert not guess['selected']
    with pytest.raises(ValueError): command('complete',asset)
    spec=work/'bounds.json'; write_json(spec,{'center':[0,0,0],'size':1,'rotation':[0,0,0]})
    corrected=command('bounds',asset,spec_file=spec)
    command('select',asset,stage='bounds',attempt=corrected['id'])
    completed=Path(command('complete',asset)['completed_folder'])
    assert inspect(completed/'block.glb')['max'][0]>.6
    assert len(Asset(asset).attempts('bounds'))==2
    source=Asset(asset).file(Asset(asset).selected('texture'),'model')
    assert GLB(source).binary==GLB(completed/'block.glb').binary
    assert command('list',root=Path(asset).parent)['assets'][0]['model']==str(completed/'block.glb')


def test_skill_helpers_do_not_import_openai_provider(work):
    assert not hasattr(importlib.import_module('core.meshy'),'OpenAI')
    # Preparing an image can run with no credentials configured.
    asset=new_asset(work)
    assert command('prepare-image',asset,stage='preview')['status']=='pending'


def test_library_lock_prevents_concurrent_mutation(work):
    root=work/'library'
    with cli.library_lock(root):
        with pytest.raises(ValueError): command('new',root=root,name='Locked')
    assert command('new',root=root,name='Available')['asset']
