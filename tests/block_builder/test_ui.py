from pathlib import Path
import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication,QFileDialog
from PySide6.QtCore import QEventLoop,QTimer
from block_builder.config import DEFAULTS
from block_builder.storage import Asset,STAGES
from block_builder.geometry import create_example_glb,inspect
from block_builder.jobs import JobManager
from block_builder.ui.wizard import Wizard
from block_builder.ui.settings import SettingsDialog
from block_builder.app import STYLE


class Preferences:
    def __init__(self): self.values=dict(DEFAULTS)
    def load(self): return dict(self.values)
    def save(self,values): self.values=dict(values)


class Credentials:
    def __init__(self): self.keys={}
    def get(self,provider): return self.keys.get(provider,'')
    def set(self,provider,key): self.keys[provider]=key
    def remove(self,provider): self.keys.pop(provider,None)
    def env_name(self,provider): return 'BLOCK_BUILDER_TEST_'+provider.upper()


@pytest.fixture(scope='module')
def app():
    application=QApplication.instance() or QApplication([])
    application.setStyleSheet(STYLE)
    yield application


def settle(ms=100):
    loop=QEventLoop(); QTimer.singleShot(ms,loop.quit); loop.exec()


def saved(wizard):
    for _ in range(60):
        if not wizard.local_pending: return
        settle(50)
    raise AssertionError('Local revision did not finish')


def test_entire_wizard_offline_import_workflow(app,tmp_path,monkeypatch):
    def unexpected_error(parent,exc): raise AssertionError(str(exc))
    monkeypatch.setattr('block_builder.ui.wizard.error',unexpected_error)
    asset=Asset.create(tmp_path/'library','UI block')
    credentials=Credentials(); preferences=Preferences(); jobs=JobManager(credentials)
    wizard=Wizard(asset,preferences,credentials,jobs); wizard.show(); settle()
    assert not wizard.model_view.errors()
    wizard.description.setPlainText('A rounded grey cube with broad sculpted rock faces.')
    wizard.generate(); saved(wizard); wizard.choose(); wizard.next()
    assert wizard.step==1
    image=tmp_path/'preview.png'; Image.new('RGB',(512,512),'grey').save(image)
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *args,**kwargs:(str(image),'Images'))
    wizard.import_result(); wizard.choose(); wizard.next()
    assert wizard.step==2 and 'NO VIEW LABELS' in wizard.turnaround_prompt.toPlainText()
    wizard.import_result(); wizard.choose(); wizard.next()
    assert wizard.step==3
    wizard.generate(); saved(wizard); wizard.choose(); wizard.next()
    assert wizard.step==4
    model=tmp_path/'cube.glb'; create_example_glb(model,protrusion=True)
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *args,**kwargs:(str(model),'GLB'))
    wizard.import_result(); wizard.choose(); wizard.next()
    assert wizard.step==5
    wizard.import_result(); wizard.choose(); wizard.next()
    assert wizard.step==6
    settle(300)
    wizard.bound_fields['size'].setValue(1)
    wizard.tile_check.setChecked(True); settle(400)
    wizard.generate(); saved(wizard); wizard.choose()
    assert asset.selected('bounds')['status']=='succeeded'
    assert not wizard.model_view.errors()
    checks=Path(__file__).resolve().parents[2]/'output'/'block_builder_checks'
    wizard.grab().save(str(checks/'wizard_bounds.png'))
    wizard.next(); assert wizard.step==7
    wizard.generate(); settle()
    revision=asset.data['completed_revision']; assert revision
    completed=asset.path/'completed'/revision/'block.glb'
    assert inspect(completed)['max'][0]>.5
    assert all(asset.selected(stage) for stage in STAGES)
    reopened=Wizard(Asset(asset.path),preferences,credentials,jobs)
    assert reopened.step==7
    reopened.steps.setCurrentRow(0)
    assert reopened.generate_btn.isEnabled()
    reopened.done(0); wizard.deleteLater(); reopened.deleteLater(); settle()


def test_settings_preserve_keys_and_save_nonsecret_preferences(app,monkeypatch):
    preferences=Preferences(); credentials=Credentials(); credentials.keys={'openai':'existing-key'}
    dialog=SettingsDialog(preferences,credentials)
    dialog.key_fields['meshy'].setText('new-test-key')
    dialog.fields['image_quality'].setCurrentText('medium')
    dialog.save()
    assert credentials.keys['openai']=='existing-key' and credentials.keys['meshy']=='new-test-key'
    assert preferences.values['image_quality']=='medium'
    assert 'new-test-key' not in str(preferences.values)
    dialog.deleteLater()


def test_description_draft_is_saved_when_wizard_closes(app,tmp_path):
    asset=Asset.create(tmp_path,'Draft')
    credentials=Credentials(); prefs=Preferences(); jobs=JobManager(credentials)
    wizard=Wizard(asset,prefs,credentials,jobs)
    wizard.description.setPlainText('Unsaved material description')
    wizard.done(0)
    reopened=Wizard(Asset(asset.path),prefs,credentials,jobs)
    assert reopened.description.toPlainText()=='Unsaved material description'
    assert reopened.asset.selected('description',False) is None
    reopened.done(0); wizard.deleteLater(); reopened.deleteLater(); settle()
