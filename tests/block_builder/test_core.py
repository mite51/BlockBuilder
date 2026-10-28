import copy
import json
import io
import numpy as np
import pytest
from PIL import Image
from block_builder.storage import Asset,Library,write_json,read_json,safe_child,STAGES
from block_builder.imaging import quadrants,split_sheet,VIEWS
from block_builder.geometry import GLB,create_example_glb,normalize_glb,inspect,suggest_bounds,normalization
from block_builder.demo import make_demo
from block_builder.prompts import presets,turnaround_prompt


def revision(asset,stage,spec=None):
    record=asset.begin(stage,spec)
    folder=asset.attempt_dir(stage,record['id'])
    write_json(folder/'result.json',{'value':record['id']})
    asset.succeed(record,{'result':'result.json'})
    return record


def test_selection_does_not_follow_new_generation(tmp_path):
    asset=Asset.create(tmp_path,'Dirt')
    first=revision(asset,'description'); asset.choose(first)
    second=revision(asset,'description')
    assert asset.selected('description')['id']==first['id']
    assert first['id']!=second['id']
    assert len(asset.attempts('description'))==2


def test_changed_upstream_invalidates_descendants_and_restore_recovers_chain(tmp_path):
    asset=Asset.create(tmp_path,'Dirt')
    desc=revision(asset,'description'); asset.choose(desc)
    preview=revision(asset,'preview'); asset.choose(preview)
    sheet=revision(asset,'turnaround'); asset.choose(sheet)
    newer=revision(asset,'description'); asset.choose(newer)
    assert asset.selected('preview',False) is None
    with pytest.raises(ValueError,match='previous inputs'): asset.choose(sheet)
    asset.choose(sheet,restore=True)
    assert asset.selected('preview')['id']==preview['id']
    assert asset.selected('description')['id']==desc['id']


def test_cannot_skip_missing_or_failed_stage(tmp_path):
    asset=Asset.create(tmp_path,'Stone')
    with pytest.raises(ValueError): asset.begin('mesh')
    record=asset.begin('description')
    with pytest.raises(ValueError): asset.choose(record)


def test_recovery_preserves_remote_ids_and_marks_ambiguous_submission(tmp_path):
    asset=Asset.create(tmp_path,'Rock')
    record=revision(asset,'description'); asset.choose(record)
    remote=asset.begin('preview'); asset.update(remote,status='running',remote={'id':'resp_test'})
    unknown=asset.begin('preview'); asset.update(unknown,status='submitting')
    restored=Asset(asset.path); restored.recover()
    assert restored.get('preview',remote['id'])['status']=='interrupted'
    assert restored.get('preview',remote['id'])['remote']['id']=='resp_test'
    assert restored.get('preview',unknown['id'])['status']=='submission_unknown'


def test_completed_revision_survives_draft_edits_and_is_portable(tmp_path):
    library=make_demo(tmp_path/'library'); asset=library.scan()[0]
    published=asset.data['completed_revision']; folder=asset.path/'completed'/published
    assert (folder/'block.glb').is_file()
    desc=revision(asset,'description'); asset.choose(desc)
    assert asset.data['completed_revision']==published
    with pytest.raises(ValueError): asset.complete()
    assert Asset(asset.path).data['completed_revision']==published
    assert not any(str(tmp_path) in p.read_text() for p in asset.path.rglob('*.json'))


def test_completion_rejects_modified_output(tmp_path):
    asset=make_demo(tmp_path).scan()[0]
    selected=asset.selected('preview'); asset.file(selected,'image').write_bytes(b'changed')
    with pytest.raises(ValueError,match='changed on disk'): asset.complete()


def test_slugs_collision_and_path_traversal(tmp_path):
    a=Asset.create(tmp_path,'../CON'); b=Asset.create(tmp_path,'../CON')
    assert a.path!=b.path and a.path.is_relative_to(tmp_path)
    assert a.path.name=='block_con'
    with pytest.raises(ValueError): safe_child(a.path,'../../secret')


def test_split_correct_order_and_odd_dimensions(tmp_path):
    image=Image.new('RGB',(101,99)); colors=['red','green','blue','white']
    rects=quadrants(image.size)
    for color,rect in zip(colors,rects): image.paste(color,rect)
    source=tmp_path/'sheet.png'; image.save(source)
    split_sheet(source,tmp_path/'split',padding=0,output_size=256)
    for name,color in zip(VIEWS,colors):
        with Image.open(tmp_path/'split'/f'{name}.png') as crop:
            assert crop.size==(256,256)
            assert crop.getpixel((128,128))==Image.new('RGB',(1,1),color).getpixel((0,0))


def test_crops_use_shared_scale_and_preserve_snow(tmp_path):
    source=tmp_path/'white.png'; Image.new('RGB',(200,200),'white').save(source)
    rects=[(0,0,100,100),(100,0,200,100),(0,100,100,200),(120,120,180,180)]
    split_sheet(source,tmp_path/'out',rects,padding=0,output_size=400)
    meta=read_json(tmp_path/'out'/'crops.json')
    assert meta['shared_scale']==4
    with Image.open(tmp_path/'out'/'right.png') as crop:
        assert crop.mode=='RGB' and crop.getpixel((200,200))==(255,255,255)
    with pytest.raises(ValueError): split_sheet(source,tmp_path/'bad',[(0,0,201,200)]*4)


def test_normalization_preserves_overhang_and_binary_data(tmp_path):
    source=tmp_path/'source.glb'; target=tmp_path/'normalized.glb'
    create_example_glb(source,protrusion=True)
    before=GLB(source)
    spec={'center':[0,0,0],'size':1,'rotation':[0,0,0]}
    meta=normalize_glb(source,target,spec)
    after=GLB(target)
    assert before.binary==after.binary
    assert before.doc['materials']==after.doc['materials']
    assert meta['mesh']['max'][0]>.6
    assert meta['logical_max']==[.5,.5,.5]


def test_normalization_preserves_embedded_texture_and_uvs(tmp_path):
    source=tmp_path/'textured.glb'; target=tmp_path/'normalized.glb'
    create_example_glb(source)
    glb=GLB(source)
    png=io.BytesIO(); Image.new('RGB',(8,8),(160,100,50)).save(png,format='PNG')
    uv=np.array([[0,0],[1,0],[1,1],[0,1]]*2,dtype='<f4').tobytes()
    binary=glb.binary+uv+png.getvalue()
    doc=glb.doc
    doc['bufferViews'].extend([{'buffer':0,'byteOffset':len(glb.binary),'byteLength':len(uv)},
                               {'buffer':0,'byteOffset':len(glb.binary)+len(uv),'byteLength':len(png.getvalue())}])
    doc['accessors'].append({'bufferView':2,'componentType':5126,'count':8,'type':'VEC2'})
    doc['meshes'][0]['primitives'][0]['attributes']['TEXCOORD_0']=2
    doc['images']=[{'bufferView':3,'mimeType':'image/png'}]; doc['textures']=[{'source':0}]
    doc['materials'][0]['pbrMetallicRoughness']['baseColorTexture']={'index':0}
    doc['buffers'][0]['byteLength']=len(binary)
    binary+=b'\0'*((-len(binary))%4)
    glb.chunks=[(0x004E4942,binary)]; glb.write(source)
    normalize_glb(source,target,{'center':[.1,.2,.3],'size':1.2,'rotation':[0,90,0]})
    before,after=GLB(source),GLB(target)
    assert before.binary==after.binary
    for key in ('images','textures','materials','accessors','bufferViews'):
        assert before.doc[key]==after.doc[key]


def test_normalization_handles_transformed_scene_nodes(tmp_path):
    source=tmp_path/'scene.glb'; target=tmp_path/'out.glb'
    create_example_glb(source)
    glb=GLB(source); glb.doc['nodes'][0]['translation']=[10,20,30]; glb.doc['nodes'][0]['scale']=[2,2,2]; glb.write(source)
    spec={'center':[10,20,30],'size':2,'rotation':[0,90,0]}
    normalize_glb(source,target,spec)
    bounds=inspect(target)
    np.testing.assert_allclose(bounds['min'],[-.5,-.5,-.5],atol=1e-6)
    np.testing.assert_allclose(bounds['max'],[.5,.5,.5],atol=1e-6)


def test_bounds_suggestion_is_surface_weighted_and_repeatable(tmp_path):
    source=tmp_path/'cube.glb'; create_example_glb(source)
    a=suggest_bounds(source); b=suggest_bounds(source)
    assert a==b
    np.testing.assert_allclose(a['center'],[0,0,0],atol=.01)
    assert .9<a['size']<=1


def test_bounds_validation(tmp_path):
    with pytest.raises(ValueError): normalization({'center':[0,0,0],'size':0})
    with pytest.raises(ValueError): normalization({'center':[float('nan'),0,0],'size':1})


def test_template_presets_and_no_labels():
    assert len(presets())==23
    assert 'Muddy Mangrove Roots' not in presets()
    prompt=turnaround_prompt('golden sand')
    assert 'ABSOLUTELY NO VIEW LABELS' in prompt
    assert 'BOTTOM' in prompt and 'golden sand' in prompt
