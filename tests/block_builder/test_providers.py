import base64
import io
import json
import httpx
import pytest
from PIL import Image
from block_builder.providers import OpenAI,Meshy,SubmissionUnknown,sanitized
from block_builder.config import DEFAULTS
from block_builder.geometry import create_example_glb


@pytest.fixture
def image(tmp_path):
    path=tmp_path/'image.png'; Image.new('RGB',(20,20),'white').save(path); return path


def test_openai_uses_astra_and_separate_image_model(image):
    body=OpenAI.payload('Generate a cube',[image],DEFAULTS)
    assert body['model']=='gpt-6-astra'
    assert body['tools'][0]['type']=='image_generation'
    assert body['tools'][0]['model']==DEFAULTS['image_model']
    assert body['background'] is True
    clean=sanitized(body)
    assert clean['input'][0]['content'][1]['image_url']['encoded_artifact_sha256']


def test_mesh_request_has_four_views_no_texture(image):
    body=Meshy.mesh_payload([image]*4,DEFAULTS)
    assert len(body['image_urls'])==4 and body['should_texture'] is False
    assert body['target_formats']==['glb']


def test_texture_multiview_exact_model_and_exclusive_fields(image,tmp_path):
    model=tmp_path/'mesh.glb'; create_example_glb(model)
    body=Meshy.texture_payload(model,[image]*4,'ignored',DEFAULTS,'task_example')
    assert body['ai_model']=='meshy-7'
    assert 'text_style_prompt' not in body and 'image_style_url' not in body
    assert body['model_url'].startswith('data:application/octet-stream;base64,')
    assert 'input_task_id' not in body  # Local accepted geometry remains usable after remote task expiry.
    assert body['enable_original_uv'] is False
    with pytest.raises(ValueError): Meshy.texture_payload(model,[image]*4,'',dict(DEFAULTS,texture_model='latest'))


def test_creation_not_retried_when_transport_outcome_unknown():
    calls=[]
    def handler(request):
        calls.append(request); raise httpx.ReadTimeout('timeout',request=request)
    api=OpenAI('test-key',httpx.MockTransport(handler))
    with pytest.raises(SubmissionUnknown): api.submit({'input':'cube'})
    assert len(calls)==1
    api.close()


def test_error_redacts_actual_key():
    def handler(request): return httpx.Response(401,json={'error':{'message':'Invalid key secret-123'}})
    api=OpenAI('secret-123',httpx.MockTransport(handler))
    with pytest.raises(RuntimeError) as caught: api.test(DEFAULTS)
    assert 'secret-123' not in str(caught.value)
    api.close()


def test_connection_check_uses_only_read_requests():
    calls=[]
    def handler(request):
        calls.append(request); return httpx.Response(200,json={'balance':100,'id':'model'})
    openai=OpenAI('test',httpx.MockTransport(handler)); meshy=Meshy('test',httpx.MockTransport(handler))
    openai.test(DEFAULTS); meshy.test()
    assert all(r.method=='GET' for r in calls)
    assert calls[-1].url.path=='/openapi/v1/balance'
    openai.close(); meshy.close()


def test_save_openai_image_and_reject_empty_result(tmp_path):
    stream=io.BytesIO(); Image.new('RGB',(16,16),'blue').save(stream,format='PNG')
    response={'output':[{'type':'image_generation_call','result':base64.b64encode(stream.getvalue()).decode()}]}
    assert OpenAI.save_result(response,tmp_path)=={'image':'image.png'}
    with Image.open(tmp_path/'image.png') as image: assert image.size==(16,16)
    with pytest.raises(RuntimeError,match='No image'): OpenAI.save_result({'output':[]},tmp_path)
