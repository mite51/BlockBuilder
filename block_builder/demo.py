"""Explicit offline fixtures; these are not AI-generated production assets."""
from pathlib import Path
import shutil
from PIL import Image,ImageDraw
from .storage import Library,write_json
from .geometry import create_example_glb,normalize_glb
from .imaging import split_sheet


def make_demo(root):
    library=Library(root)
    existing={a.data['name'] for a in library.scan()}
    for name,color in [('Demo · Earth',(.55,.34,.17,1)),('Demo · Slate',(.24,.29,.36,1)),('Demo · Snow',(.85,.92,1,1)),('Demo · Clay',(.44,.61,.69,1))]:
        if name in existing: continue
        asset=library.create(name)
        rec=asset.begin('description',{'description':'Offline demonstration cube for testing the authoring workflow.','references':[]})
        folder=asset.attempt_dir('description',rec['id']); write_json(folder/'description.json',rec['spec']); asset.succeed(rec,{'description':'description.json'}); asset.choose(rec)
        rgb=tuple(int(v*255) for v in color[:3])
        for stage in ('preview','turnaround'):
            rec=asset.begin(stage,{'offline_demo':True})
            folder=asset.attempt_dir(stage,rec['id']); im=Image.new('RGB',(512,512),'white'); draw=ImageDraw.Draw(im)
            centers=[(256,256)] if stage=='preview' else [(128,128),(384,128),(128,384),(384,384)]
            radius=105 if stage=='preview' else 70
            for x,y in centers:
                draw.polygon([(x,y-radius),(x+radius,y-radius//2),(x,y),(x-radius,y-radius//2)],fill=rgb)
                draw.polygon([(x-radius,y-radius//2),(x,y),(x,y+radius),(x-radius,y+radius//2)],fill=tuple(int(v*.7) for v in rgb))
                draw.polygon([(x,y),(x+radius,y-radius//2),(x+radius,y+radius//2),(x,y+radius)],fill=tuple(int(v*.85) for v in rgb))
            im.save(folder/'image.png'); asset.succeed(rec,{'image':'image.png'}); asset.choose(rec)
        rec=asset.begin('split',{}); folder=asset.attempt_dir('split',rec['id'])
        outputs=split_sheet(asset.file(asset.selected('turnaround'),'image'),folder,output_size=512)
        asset.succeed(rec,outputs); asset.choose(rec)
        for stage in ('mesh','texture'):
            rec=asset.begin(stage,{'offline_demo':True}); folder=asset.attempt_dir(stage,rec['id'])
            create_example_glb(folder/'model.glb',color,protrusion=True); asset.succeed(rec,{'model':'model.glb'}); asset.choose(rec)
        spec={'center':[0,0,0],'size':1,'rotation':[0,0,0]}
        rec=asset.begin('bounds',spec); folder=asset.attempt_dir('bounds',rec['id'])
        metadata=normalize_glb(asset.file(asset.selected('texture'),'model'),folder/'model.glb',spec)
        write_json(folder/'bounds.json',metadata); asset.succeed(rec,{'model':'model.glb','bounds':'bounds.json'}); asset.choose(rec)
        asset.complete()
    return library
