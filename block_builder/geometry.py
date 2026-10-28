"""Static GLB inspection and lossless scene-root normalization.

Binary geometry, UVs, embedded textures and material definitions are preserved.
Compressed/animated models are rejected explicitly rather than misinterpreted.
"""
from __future__ import annotations
import copy
import json
import math
import struct
from pathlib import Path
import numpy as np
from .storage import atomic_bytes, write_json

JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942


class GLB:
    def __init__(self, path):
        raw = Path(path).read_bytes()
        if len(raw) < 20 or struct.unpack_from('<4sII', raw) != (b'glTF', 2, len(raw)):
            raise ValueError('Expected a valid glTF 2 binary (.glb) file.')
        self.chunks = []
        offset = 12
        while offset < len(raw):
            length, kind = struct.unpack_from('<II', raw, offset)
            offset += 8
            if offset + length > len(raw):
                raise ValueError('Truncated GLB chunk.')
            self.chunks.append((kind, raw[offset:offset+length]))
            offset += length
        self.doc = json.loads(next(data for kind,data in self.chunks if kind == JSON_CHUNK))
        self.binary = next((data for kind,data in self.chunks if kind == BIN_CHUNK), b'')
        if any('uri' in b for b in self.doc.get('buffers', [])) or any('uri' in i for i in self.doc.get('images', [])):
            raise ValueError('Use a self-contained GLB with embedded buffers and textures.')
        if self.doc.get('animations') or self.doc.get('skins'):
            raise ValueError('Block assets must be static, without skinning or animation.')

    def write(self, path, doc=None):
        data = json.dumps(doc or self.doc, separators=(',', ':'), allow_nan=False).encode('utf-8')
        data += b' ' * ((-len(data)) % 4)
        chunks = [(JSON_CHUNK, data)] + [(kind, content) for kind,content in self.chunks if kind != JSON_CHUNK]
        body = b''.join(struct.pack('<II', len(content), kind) + content for kind,content in chunks)
        atomic_bytes(Path(path), struct.pack('<4sII', b'glTF', 2, len(body)+12) + body)

    def accessor(self, index):
        acc = self.doc['accessors'][index]
        if 'sparse' in acc or 'bufferView' not in acc:
            raise ValueError('Sparse or compressed geometry needs an uncompressed GLB export.')
        view = self.doc['bufferViews'][acc['bufferView']]
        if view.get('buffer', 0) != 0 or 'EXT_meshopt_compression' in view.get('extensions', {}):
            raise ValueError('Compressed geometry needs an uncompressed GLB export.')
        dtype = {5120:'i1',5121:'u1',5122:'<i2',5123:'<u2',5125:'<u4',5126:'<f4'}[acc['componentType']]
        width = {'SCALAR':1,'VEC2':2,'VEC3':3,'VEC4':4,'MAT4':16}[acc['type']]
        item = np.dtype(dtype).itemsize
        stride = view.get('byteStride', item*width)
        offset = view.get('byteOffset',0)+acc.get('byteOffset',0)
        values = np.ndarray((acc['count'],width), dtype=dtype, buffer=self.binary,
                            offset=offset, strides=(stride,item)).copy()
        if acc.get('normalized') and acc['componentType'] != 5126:
            info = np.iinfo(dtype)
            values = np.maximum(values.astype(float)/info.max, -1)
        return values

    def triangles(self):
        parts = []
        nodes = self.doc.get('nodes', [])
        scene = self.doc.get('scenes', [{}])[self.doc.get('scene', 0)]
        roots = scene.get('nodes', [])
        if not roots:
            raise ValueError('GLB has no active scene.')

        def visit(index, parent, ancestors):
            if index in ancestors:
                raise ValueError('Cyclic GLB scene.')
            node = nodes[index]
            matrix = parent @ node_matrix(node)
            if 'mesh' in node:
                for primitive in self.doc['meshes'][node['mesh']]['primitives']:
                    if primitive.get('mode',4) != 4 or 'KHR_draco_mesh_compression' in primitive.get('extensions',{}):
                        raise ValueError('Use an uncompressed triangle mesh GLB.')
                    if primitive.get('targets'):
                        raise ValueError('Morph targets are not supported for static blocks.')
                    verts = self.accessor(primitive['attributes']['POSITION']).astype(float)
                    verts = verts @ matrix[:3,:3].T + matrix[:3,3]
                    indices = self.accessor(primitive['indices']).reshape(-1) if 'indices' in primitive else np.arange(len(verts))
                    if len(indices) % 3:
                        raise ValueError('Invalid triangle indices.')
                    parts.append(verts[indices.reshape(-1,3)])
            for child in node.get('children', []):
                visit(child, matrix, ancestors | {index})
        for index in roots:
            visit(index, np.eye(4), set())
        if not parts:
            raise ValueError('No triangle geometry found.')
        triangles = np.concatenate(parts)
        if not np.isfinite(triangles).all():
            raise ValueError('Mesh contains non-finite positions.')
        return triangles


def node_matrix(node):
    if 'matrix' in node:
        return np.array(node['matrix'], dtype=float).reshape(4,4).T
    x,y,z,w = node.get('rotation', [0,0,0,1])
    rotation = np.array([[1-2*y*y-2*z*z, 2*x*y-2*z*w, 2*x*z+2*y*w],
                         [2*x*y+2*z*w, 1-2*x*x-2*z*z, 2*y*z-2*x*w],
                         [2*x*z-2*y*w, 2*y*z+2*x*w, 1-2*x*x-2*y*y]])
    result = np.eye(4)
    result[:3,:3] = rotation @ np.diag(node.get('scale',[1,1,1]))
    result[:3,3] = node.get('translation',[0,0,0])
    return result


def rotation_xyz(degrees):
    x,y,z = np.radians(degrees)
    cx,sx,cy,sy,cz,sz = math.cos(x),math.sin(x),math.cos(y),math.sin(y),math.cos(z),math.sin(z)
    rx = np.array([[1,0,0],[0,cx,-sx],[0,sx,cx]])
    ry = np.array([[cy,0,sy],[0,1,0],[-sy,0,cy]])
    rz = np.array([[cz,-sz,0],[sz,cz,0],[0,0,1]])
    return rz @ ry @ rx


def normalization(spec):
    center = np.array(spec['center'], dtype=float)
    size = float(spec['size'])
    rotation = rotation_xyz(spec.get('rotation',[0,0,0]))
    if center.shape != (3,) or not np.isfinite(center).all() or not math.isfinite(size) or size <= 1e-9 or not np.isfinite(rotation).all():
        raise ValueError('Bounds need a finite center, rotation and positive side length.')
    transform = np.eye(4)
    transform[:3,:3] = rotation.T / size
    transform[:3,3] = -(rotation.T @ center) / size
    return transform


def inspect(path):
    triangles = GLB(path).triangles()
    points = triangles.reshape(-1,3)
    return {'triangles':len(triangles), 'min': points.min(axis=0).tolist(), 'max':points.max(axis=0).tolist()}


def suggest_bounds(path):
    triangles = GLB(path).triangles()
    areas = np.linalg.norm(np.cross(triangles[:,1]-triangles[:,0],triangles[:,2]-triangles[:,0]),axis=1)/2
    if areas.sum() <= 0:
        raise ValueError('Mesh has no measurable surface area.')
    rng = np.random.default_rng(23)
    chosen = triangles[rng.choice(len(triangles), 24000, p=areas/areas.sum())]
    u = np.sqrt(rng.random((len(chosen),1)))
    v = rng.random((len(chosen),1))
    samples = (1-u)*chosen[:,0]+u*(1-v)*chosen[:,1]+u*v*chosen[:,2]
    lo,hi = np.quantile(samples,[0.04,0.96],axis=0)
    return {'center':((hi+lo)/2).tolist(), 'size':float(np.median(hi-lo)),
            'rotation':[0,0,0], 'method':'area-weighted surface quantiles (4–96%); user correction required'}


def normalize_glb(source, target, spec):
    glb = GLB(source)
    transform = normalization(spec)
    doc = copy.deepcopy(glb.doc)
    for scene in doc['scenes']:
        root = {'name':'BlockBuilderPlacement', 'matrix':transform.T.flatten().tolist(), 'children':scene.get('nodes',[])}
        scene['nodes'] = [len(doc['nodes'])]
        doc['nodes'].append(root)
    glb.write(target, doc)
    stats = inspect(target)
    metadata = {**spec, 'logical_min':[-.5,-.5,-.5], 'logical_max':[.5,.5,.5],
                'coordinate_system':'right-handed; Y up; X right; Z front',
                'transform_column_major':transform.T.flatten().tolist(), 'mesh':stats,
                'protrusion_negative':np.maximum(-.5-np.array(stats['min']),0).tolist(),
                'protrusion_positive':np.maximum(np.array(stats['max'])-.5,0).tolist()}
    return metadata


def create_example_glb(path, color=(.52,.33,.18,1), protrusion=False):
    # Small local fixture, also used for the explicit demo library.
    verts = np.array([[-.5,-.5,-.5],[.5,-.5,-.5],[.5,.5,-.5],[-.5,.5,-.5],
                      [-.5,-.5,.5],[.5,-.5,.5],[.5,.5,.5],[-.5,.5,.5]],dtype='<f4')
    if protrusion:
        verts[6] += [.12,.06,.08]
    indices = np.array([0,2,1,0,3,2,4,5,6,4,6,7,0,1,5,0,5,4,3,7,6,3,6,2,0,4,7,0,7,3,1,2,6,1,6,5],dtype='<u2')
    data = verts.tobytes()+indices.tobytes()
    doc = {'asset':{'version':'2.0'},'scene':0,'scenes':[{'nodes':[0]}], 'nodes':[{'mesh':0}],
           'meshes':[{'primitives':[{'attributes':{'POSITION':0},'indices':1,'material':0}]}],
           'materials':[{'pbrMetallicRoughness':{'baseColorFactor':list(color),'metallicFactor':0,'roughnessFactor':.9}}],
           'buffers':[{'byteLength':len(data)}],
           'bufferViews':[{'buffer':0,'byteOffset':0,'byteLength':verts.nbytes},{'buffer':0,'byteOffset':verts.nbytes,'byteLength':indices.nbytes}],
           'accessors':[{'bufferView':0,'componentType':5126,'count':8,'type':'VEC3','min':verts.min(axis=0).tolist(),'max':verts.max(axis=0).tolist()},
                        {'bufferView':1,'componentType':5123,'count':len(indices),'type':'SCALAR'}]}
    obj = GLB.__new__(GLB)
    obj.doc = doc
    obj.chunks = [(BIN_CHUNK, data)]
    obj.write(path)
