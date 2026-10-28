from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

STAGES = ('description', 'preview', 'turnaround', 'split', 'mesh', 'texture', 'bounds')
ACTIVE = {'pending', 'submitting', 'running', 'downloading'}


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '_' + uuid.uuid4().hex[:10]


def atomic_bytes(path: Path, data: bytes):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with tmp.open('wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write_json(path, value):
    atomic_bytes(Path(path), json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False).encode('utf-8'))


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def safe_child(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Asset paths must stay inside the asset folder.')
    return path


class Asset:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.lock = threading.RLock()
        self.data = read_json(self.path / 'asset.json')
        if self.data.get('schema_version') != 1:
            raise ValueError('Unsupported asset schema version.')

    @classmethod
    def create(cls, root, name):
        if not name.strip():
            raise ValueError('Enter an asset name.')
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r'[^a-z0-9_-]+', '_', name.lower()).strip('_')[:72] or 'block'
        if slug.upper() in {'CON', 'PRN', 'AUX', 'NUL', *('COM'+str(i) for i in range(1,10)), *('LPT'+str(i) for i in range(1,10))}:
            slug = 'block_' + slug
        folder = root / slug
        for i in range(1, 10000):
            try:
                folder.mkdir()
                break
            except FileExistsError:
                folder = root / f'{slug}_{i+1}'
        else:
            raise ValueError('Could not allocate an asset folder.')
        write_json(folder / 'asset.json', {'schema_version': 1, 'id': str(uuid.uuid4()),
                   'name': name.strip(), 'created_at': now(), 'selections': {},
                   'completed_revision': None, 'draft': {}, 'last_step': 0})
        return cls(folder)

    def save(self):
        with self.lock:
            self.data['updated_at'] = now()
            write_json(self.path / 'asset.json', self.data)

    def event(self, kind, **details):
        with self.lock:
            folder=self.path/'logs'
            folder.mkdir(exist_ok=True)
            with (folder/'events.jsonl').open('a',encoding='utf-8') as stream:
                stream.write(json.dumps({'time':now(),'event':kind,**details},ensure_ascii=False)+'\n')

    def attempts(self, stage):
        results = []
        for p in sorted((self.path / 'stages' / stage).glob('*/attempt.json')):
            try:
                results.append(read_json(p))
            except (OSError, ValueError):
                continue
        return results

    def attempt_dir(self, stage, attempt_id):
        if stage not in STAGES or not re.fullmatch(r'[A-Za-z0-9_-]+', attempt_id):
            raise ValueError('Invalid stage or attempt ID.')
        return safe_child(self.path, f'stages/{stage}/{attempt_id}')

    def get(self, stage, attempt_id):
        return read_json(self.attempt_dir(stage, attempt_id) / 'attempt.json')

    def compatible(self, record):
        return all(self.data['selections'].get(k) == v for k, v in record['inputs'].items())

    def selected(self, stage, require=True):
        selected_id = self.data['selections'].get(stage)
        record = self.get(stage, selected_id) if selected_id else None
        if record and (record['status'] != 'succeeded' or not self.compatible(record)):
            record = None
        if require and record is None:
            raise ValueError(f'Select a current {stage} result first.')
        return record

    def begin(self, stage, spec=None, settings=None):
        with self.lock:
            inputs = {}
            for parent in STAGES[:STAGES.index(stage)]:
                inputs[parent] = self.selected(parent)['id']
            record = {'id': uid(), 'stage': stage, 'created_at': now(), 'status': 'pending',
                      'inputs': inputs, 'spec': spec or {}, 'settings': settings or {},
                      'outputs': {}, 'hashes': {}, 'remote': {}, 'error': None}
            folder = self.attempt_dir(stage, record['id'])
            folder.mkdir(parents=True)
            write_json(folder / 'attempt.json', record)
            self.event('attempt_created',stage=stage,attempt_id=record['id'],inputs=inputs)
            return record

    def update(self, record, **changes):
        with self.lock:
            record.update(changes)
            record['updated_at'] = now()
            write_json(self.attempt_dir(record['stage'], record['id']) / 'attempt.json', record)
            if 'status' in changes:
                self.event('attempt_status',stage=record['stage'],attempt_id=record['id'],status=changes['status'])

    def succeed(self, record, outputs):
        hashes = {}
        for key, relative in outputs.items():
            path = safe_child(self.attempt_dir(record['stage'], record['id']), relative)
            if not path.is_file():
                raise ValueError(f'Missing output: {relative}')
            hashes[key] = digest(path)
        self.update(record, status='succeeded', outputs=outputs, hashes=hashes, error=None)

    def file(self, record, key):
        return safe_child(self.attempt_dir(record['stage'], record['id']), record['outputs'][key])

    def choose(self, record, restore=False):
        with self.lock:
            if record['status'] != 'succeeded':
                raise ValueError('Only successful attempts can be selected.')
            if not restore and not self.compatible(record):
                raise ValueError('This attempt uses previous inputs. Restore its input chain to select it.')
            for stage, attempt_id in record['inputs'].items():
                ancestor = self.get(stage, attempt_id)
                if ancestor['status'] != 'succeeded':
                    raise ValueError('An input attempt is unavailable.')
            if restore:
                self.data['selections'].update(record['inputs'])
            self.data['selections'][record['stage']] = record['id']
            self.save()
            self.event('selected',stage=record['stage'],attempt_id=record['id'],restored_inputs=restore)

    def add_source(self, source):
        from PIL import Image, ImageOps
        source = Path(source)
        with Image.open(source) as im:
            im.verify()
        source_id = uid()
        folder = self.path / 'sources'
        folder.mkdir(exist_ok=True)
        original = folder / (source_id + '_original' + source.suffix.lower())
        shutil.copy2(source, original)
        working = folder / (source_id + '.png')
        with Image.open(original) as im:
            ImageOps.exif_transpose(im).convert('RGBA').save(working)
        return {'path': working.relative_to(self.path).as_posix(),
                'original': original.relative_to(self.path).as_posix(),
                'name': source.name, 'sha256': digest(original), 'working_sha256': digest(working)}

    def recover(self):
        recovered = []
        for stage in STAGES:
            for record in self.attempts(stage):
                if record['status'] in ACTIVE:
                    if stage not in ('preview','turnaround','mesh','texture'):
                        status = 'failed'
                    else:
                        status = 'interrupted' if record.get('remote', {}).get('id') or record['status'] == 'pending' else 'submission_unknown'
                    self.update(record, status=status, error='Application closed before this attempt finished.')
                    recovered.append(record)
        return recovered

    def complete(self, thumbnail=None):
        with self.lock:
            records = {stage: self.selected(stage) for stage in STAGES}
            for record in records.values():
                for key, expected in record['hashes'].items():
                    if digest(self.file(record, key)) != expected:
                        raise ValueError('An output has changed on disk. Import it as a new attempt.')
            revision = uid()
            folder = self.path / 'completed' / revision
            folder.mkdir(parents=True)
            bounds = records['bounds']
            shutil.copy2(self.file(bounds, 'model'), folder / 'block.glb')
            shutil.copy2(self.file(bounds, 'bounds'), folder / 'bounds.json')
            if thumbnail and Path(thumbnail).is_file():
                shutil.copy2(thumbnail, folder / 'thumbnail.png')
            else:
                shutil.copy2(self.file(records['preview'], 'image'), folder / 'thumbnail.png')
            write_json(folder / 'selection.json', {'created_at': now(), 'selections': dict(self.data['selections'])})
            self.data['completed_revision'] = revision
            self.save()
            self.event('completed',revision=revision,selections=dict(self.data['selections']))
            return folder


class Library:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.assets = {}
        self.errors = []

    def scan(self):
        self.root.mkdir(parents=True, exist_ok=True)
        self.errors = []
        found = {}
        for manifest in sorted(self.root.glob('*/asset.json')):
            try:
                key = str(manifest.parent.resolve())
                found[key] = self.assets.get(key) or Asset(manifest.parent)
            except (ValueError, OSError) as exc:
                self.errors.append(f'{manifest.parent.name}: {exc}')
        self.assets = found
        return list(found.values())

    def create(self, name):
        asset = Asset.create(self.root, name)
        self.assets[str(asset.path)] = asset
        return asset
