import os
from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,QTabWidget,QWidget,
    QLineEdit,QLabel,QComboBox,QSpinBox,QCheckBox,QFileDialog,QDialogButtonBox)
from ..providers import OpenAI,Meshy
from ..config import DEFAULTS
from .common import button,error


class ConnectionTest(QObject):
    done = Signal(str)
    finished = Signal()
    def __init__(self, provider, key, settings):
        super().__init__()
        self.provider,self.key,self.settings = provider,key,settings

    def run(self):
        api = None
        try:
            api = OpenAI(self.key) if self.provider=='openai' else Meshy(self.key)
            self.done.emit(api.test(self.settings))
        except Exception as exc:
            message = str(exc).replace(self.key,'[redacted]') if self.key else str(exc)
            self.done.emit(message)
        finally:
            if api:
                api.close()
            self.key=''
            self.finished.emit()


class SettingsDialog(QDialog):
    def __init__(self, preferences, credentials, parent=None):
        super().__init__(parent)
        self.preferences,self.credentials = preferences,credentials
        self.setWindowTitle('Block Builder — Settings')
        self.resize(680,620)
        self.values = preferences.load()
        self.fields = {}
        self.key_fields = {}
        self.removed = set()
        self.tests = []
        layout = QVBoxLayout(self)
        title=QLabel('Settings'); title.setObjectName('title'); layout.addWidget(title)
        tabs=QTabWidget(); layout.addWidget(tabs)
        connections=QWidget(); form=QFormLayout(connections); tabs.addTab(connections,'Connections')
        note=QLabel('Keys are stored in Windows Credential Manager.\nThey are never included in asset folders. Leave a field blank to keep its saved key.')
        note.setWordWrap(True); form.addRow(note)
        for provider in ('openai','meshy'):
            field=QLineEdit(); field.setEchoMode(QLineEdit.EchoMode.Password)
            try:
                present=bool(credentials.get(provider))
                field.setPlaceholderText('Key configured — enter a replacement' if present else 'Paste API key')
            except Exception:
                field.setPlaceholderText('Paste API key')
            self.key_fields[provider]=field
            row=QWidget(); line=QHBoxLayout(row); line.setContentsMargins(0,0,0,0)
            line.addWidget(field); line.addWidget(button('Remove',lambda checked=False,p=provider:self.remove_key(p)))
            form.addRow('OpenAI' if provider=='openai' else 'Meshy',row)
            status=QLabel('Environment override is active.' if os.environ.get(credentials.env_name(provider)) else '')
            status.setWordWrap(True)
            test=button('Test connection',lambda checked=False,p=provider,s=status:self.test(p,s))
            form.addRow(test,status)
        generation=QWidget(); form=QFormLayout(generation); tabs.addTab(generation,'Generation')
        self.add_text(form,'Astra inference model','astra_model')
        self.add_text(form,'Image model','image_model')
        self.add_combo(form,'Image size','image_size',['1024x1024','2048x2048','auto'])
        self.add_combo(form,'Image quality','image_quality',['high','medium','low','auto'])
        self.add_combo(form,'Geometry model','mesh_model',['meshy-7.1','meshy-6'])
        self.add_combo(form,'Geometry detail','geometry_resolution',['standard','2k'])
        self.add_spin(form,'Target triangles','polycount',100,300000,1000)
        self.add_combo(form,'Texture guidance','texture_mode',['multiview','image','text'])
        self.add_combo(form,'Texture model','texture_model',['meshy-7','meshy-6','meshy-6-lite'])
        self.add_combo(form,'Texture size','texture_resolution',['2k','4k','8k'])
        self.add_check(form,'Generate PBR maps','enable_pbr')
        self.add_spin(form,'Concurrent generation jobs','concurrency',1,4)
        hint=QLabel('Multiview texture guidance requires meshy-7. Model access and supported image settings depend on your account.')
        hint.setWordWrap(True); form.addRow(hint)
        library=QWidget(); form=QFormLayout(library); tabs.addTab(library,'Library & viewer')
        self.add_text(form,'Asset library','library')
        form.addRow(button('Choose library folder…',self.browse))
        form.addRow(QLabel('Changing this folder switches libraries; it does not move existing assets.'))
        self.add_check(form,'Rotate library previews','auto_rotate')
        self.add_combo(form,'Preview quality','preview_quality',['medium','high'])
        actions=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        actions.accepted.connect(self.save); actions.rejected.connect(self.reject); layout.addWidget(actions)

    def add_text(self,form,label,key):
        field=QLineEdit(str(self.values[key])); self.fields[key]=field; form.addRow(label,field)

    def add_combo(self,form,label,key,items):
        field=QComboBox(); field.addItems(items)
        if str(self.values[key]) not in items: field.addItem(str(self.values[key]))
        field.setCurrentText(str(self.values[key])); self.fields[key]=field; form.addRow(label,field)

    def add_spin(self,form,label,key,lo,hi,step=1):
        field=QSpinBox(); field.setRange(lo,hi); field.setSingleStep(step); field.setValue(self.values[key])
        self.fields[key]=field; form.addRow(label,field)

    def add_check(self,form,label,key):
        field=QCheckBox(); field.setChecked(self.values[key]); self.fields[key]=field; form.addRow(label,field)

    def collect(self):
        values=dict(self.values)
        for key,field in self.fields.items():
            if isinstance(field,QLineEdit): values[key]=field.text().strip()
            elif isinstance(field,QComboBox): values[key]=field.currentText()
            elif isinstance(field,QCheckBox): values[key]=field.isChecked()
            else: values[key]=field.value()
        return values

    def remove_key(self,provider):
        self.removed.add(provider); self.key_fields[provider].clear()
        self.key_fields[provider].setPlaceholderText('Will remove saved key when you Save')

    def browse(self):
        path=QFileDialog.getExistingDirectory(self,'Asset library',self.fields['library'].text())
        if path: self.fields['library'].setText(path)

    def test(self,provider,status):
        try:
            key=self.key_fields[provider].text().strip() or self.credentials.get(provider)
            if not key: raise ValueError('Enter an API key first.')
            status.setText('Checking connection…')
            thread=QThread(self); worker=ConnectionTest(provider,key,self.collect()); worker.moveToThread(thread)
            thread.started.connect(worker.run); worker.done.connect(status.setText)
            worker.finished.connect(thread.quit); thread.finished.connect(worker.deleteLater)
            self.tests.append((thread,worker)); thread.start()
        except Exception as exc: error(self,exc)

    def save(self):
        try:
            values=self.collect()
            if not all(values[k] for k in ('library','astra_model','image_model')): raise ValueError('Library and model names are required.')
            if values['texture_mode']=='multiview' and values['texture_model']!='meshy-7': raise ValueError('Choose meshy-7 for multiview texturing.')
            if values['texture_model']=='meshy-6-lite' and values['texture_resolution']!='2k': raise ValueError('meshy-6-lite textures must use 2k.')
            if values['mesh_model']=='meshy-6' and values['geometry_resolution']=='2k': raise ValueError('Ultra geometry requires meshy-7.1.')
            for provider,field in self.key_fields.items():
                if field.text().strip(): self.credentials.set(provider,field.text())
                elif provider in self.removed: self.credentials.remove(provider)
            self.preferences.save(values); self.accept()
        except Exception as exc: error(self,exc)

    def done(self,result):
        if any(thread.isRunning() for thread,_ in self.tests):
            error(self,'A connection check is still running. Wait for its result before closing Settings.')
            return
        super().done(result)
