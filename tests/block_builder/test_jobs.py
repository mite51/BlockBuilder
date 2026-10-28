import threading
from pathlib import Path
import pytest
from PIL import Image
from PySide6.QtCore import QCoreApplication
from block_builder.storage import Asset,write_json,read_json
from block_builder.config import DEFAULTS
from block_builder.jobs import Worker
from block_builder.providers import SubmissionUnknown


@pytest.fixture
def job_asset(tmp_path):
    app=QCoreApplication.instance()
    # Workers are executed directly here; queued dispatch is covered by UI tests.
    asset=Asset.create(tmp_path,'Job test')
    record=asset.begin('description',{'description':'Rock','references':[]})
    folder=asset.attempt_dir('description',record['id']); write_json(folder/'description.json',record['spec'])
    asset.succeed(record,{'description':'description.json'}); asset.choose(record)
    return asset


class FakeOpenAI:
    submissions=0
    polls=[]
    def __init__(self,key): pass
    def close(self): pass
    @staticmethod
    def payload(prompt,images,settings): return {'prompt':prompt}
    def submit(self,payload):
        FakeOpenAI.submissions+=1
        return {'id':'resp_existing'}
    def poll(self,task_id):
        FakeOpenAI.polls.append(task_id)
        return {'id':task_id,'status':'completed','output':[]}
    @staticmethod
    def save_result(response,folder):
        Image.new('RGB',(20,20),'grey').save(Path(folder)/'image.png')
        return {'image':'image.png'}


def test_job_persists_remote_id_without_selecting_result(job_asset,monkeypatch):
    FakeOpenAI.submissions=0; FakeOpenAI.polls=[]
    monkeypatch.setattr('block_builder.jobs.OpenAI',FakeOpenAI)
    record=job_asset.begin('preview',{'prompt':'cube'},DEFAULTS)
    Worker(job_asset,record,'fake').run()
    saved=job_asset.get('preview',record['id'])
    assert saved['status']=='succeeded' and saved['remote']['id']=='resp_existing'
    assert FakeOpenAI.submissions==1
    assert job_asset.selected('preview',False) is None
    assert job_asset.file(saved,'image').exists()


def test_resume_polls_existing_job_without_resubmitting(job_asset,monkeypatch):
    FakeOpenAI.submissions=0; FakeOpenAI.polls=[]
    monkeypatch.setattr('block_builder.jobs.OpenAI',FakeOpenAI)
    record=job_asset.begin('preview',{'prompt':'cube'},DEFAULTS)
    job_asset.update(record,status='interrupted',remote={'provider':'openai','endpoint':'responses','id':'resp_recover'})
    Worker(job_asset,record,'fake').run()
    assert FakeOpenAI.submissions==0 and FakeOpenAI.polls==['resp_recover']
    assert job_asset.get('preview',record['id'])['status']=='succeeded'


def test_unknown_submission_stays_unknown(job_asset,monkeypatch):
    class Unknown(FakeOpenAI):
        def submit(self,payload): raise SubmissionUnknown('Uncertain submission')
    monkeypatch.setattr('block_builder.jobs.OpenAI',Unknown)
    record=job_asset.begin('preview',{'prompt':'cube'},DEFAULTS)
    Worker(job_asset,record,'fake').run()
    assert job_asset.get('preview',record['id'])['status']=='submission_unknown'


def test_paused_job_keeps_id_for_later_recovery(job_asset,monkeypatch):
    monkeypatch.setattr('block_builder.jobs.OpenAI',FakeOpenAI)
    record=job_asset.begin('preview',{'prompt':'cube'},DEFAULTS)
    job_asset.update(record,status='interrupted',remote={'id':'resp_keep','provider':'openai','endpoint':'responses'})
    worker=Worker(job_asset,record,'fake'); worker.stop.set(); worker.run()
    saved=job_asset.get('preview',record['id'])
    assert saved['status']=='interrupted' and saved['remote']['id']=='resp_keep'


def test_job_input_snapshot_survives_new_description_selection(job_asset,monkeypatch):
    seen=[]
    class Inspect(FakeOpenAI):
        @staticmethod
        def payload(prompt,images,settings): seen.append(prompt); return {'prompt':prompt}
    monkeypatch.setattr('block_builder.jobs.OpenAI',Inspect)
    record=job_asset.begin('preview',{'prompt':'Original accepted description'},DEFAULTS)
    other=job_asset.begin('description',{'description':'Changed','references':[]})
    folder=job_asset.attempt_dir('description',other['id']); write_json(folder/'result.json',other['spec'])
    job_asset.succeed(other,{'description':'result.json'}); job_asset.choose(other)
    Worker(job_asset,record,'fake').run()
    assert seen==['Original accepted description']
    assert job_asset.get('preview',record['id'])['status']=='succeeded'
    assert not job_asset.compatible(record)
