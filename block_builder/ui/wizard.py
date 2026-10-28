from __future__ import annotations
import re
import shutil
from pathlib import Path
from PIL import Image,ImageOps
from PySide6.QtCore import Qt, QTimer, QSize, QUrl
from PySide6.QtGui import QIcon,QDesktopServices,QPixmap
from PySide6.QtWidgets import (QDialog,QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,QLineEdit,
    QTextEdit,QComboBox,QListWidget,QListWidgetItem,QSplitter,QStackedWidget,QScrollArea,
    QFileDialog,QCheckBox,QSpinBox,QDoubleSpinBox,QTableWidget,QTableWidgetItem,QHeaderView,
    QInputDialog,QMessageBox,QGridLayout)
from ..storage import STAGES,ACTIVE,write_json,read_json,safe_child,uid,atomic_bytes,digest
from ..imaging import VIEWS,quadrants,split_sheet
from ..geometry import GLB,inspect,suggest_bounds,normalize_glb
from ..prompts import presets,preview_prompt,turnaround_prompt
from .common import button,error,ImageView,BlockView,Tasks
from .settings import SettingsDialog

LABELS = ['Describe','Preview','Four-view sheet','Split views','Mesh','Texture','Placement bounds','Complete']


class Wizard(QDialog):
    def __init__(self,asset,preferences,credentials,jobs,parent=None):
        super().__init__(parent)
        self.asset,self.preferences,self.credentials,self.jobs = asset,preferences,credentials,jobs
        self.setWindowTitle('Block Builder — '+asset.data['name'])
        self.resize(1320,860)
        self.setMinimumSize(1080,720)
        self.references=[]
        self.record=None
        self.step=0
        self.loading=False
        self.bounds_source=None
        self.bounds_cache=[]
        self.crop_source=None
        self.geometry_cache={}
        self.restored_draft=asset.data.get('draft',{})
        self.tasks=Tasks(self)
        self.preview_request=0
        self.bounds_edit_revision=0
        self.local_pending=0
        self._description_dirty=False
        layout=QVBoxLayout(self)
        top=QHBoxLayout(); layout.addLayout(top)
        self.title=QLabel(asset.data['name']); self.title.setObjectName('title'); top.addWidget(self.title)
        top.addStretch(); top.addWidget(button('Open asset folder',lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(asset.path)))))
        top.addWidget(button('Settings',self.settings)); top.addWidget(button('Save & return to library',self.accept))
        splitter=QSplitter(); layout.addWidget(splitter,1)
        self.steps=QListWidget(); self.steps.setFixedWidth(170); self.steps.addItems(LABELS)
        splitter.addWidget(self.steps)
        center=QWidget(); center_layout=QVBoxLayout(center); center_layout.setContentsMargins(6,0,6,0)
        self.stage_title=QLabel(); self.stage_title.setObjectName('subtitle'); center_layout.addWidget(self.stage_title)
        self.viewer_stack=QStackedWidget(); center_layout.addWidget(self.viewer_stack,1)
        self.image_view=ImageView(); self.viewer_stack.addWidget(self.image_view)
        viewer_wrap=QWidget(); vl=QVBoxLayout(viewer_wrap); vl.setContentsMargins(0,0,0,0)
        cameras=QHBoxLayout(); vl.addLayout(cameras)
        self.model_view=BlockView()
        self.model_view.set_value('highQuality',preferences.load()['preview_quality']=='high')
        for name in ('Orbit','Front','Side','Top'):
            cameras.addWidget(button(name,lambda checked=False,n=name:self.model_view.preset(n)))
        wire=QCheckBox('Wireframe'); wire.toggled.connect(lambda v:self.model_view.set_value('wireframe',v)); cameras.addWidget(wire)
        cameras.addStretch(); vl.addWidget(self.model_view,1); self.viewer_stack.addWidget(viewer_wrap)
        crops=QWidget(); cl=QGridLayout(crops); self.crop_views=[]
        for i,name in enumerate(VIEWS):
            wrapper=QWidget(); box=QVBoxLayout(wrapper); label=QLabel(name.upper()); label.setObjectName('muted'); box.addWidget(label)
            image=ImageView(); box.addWidget(image,1); self.crop_views.append(image); cl.addWidget(wrapper,i//2,i%2)
        self.viewer_stack.addWidget(crops)
        self.complete_text=QLabel(); self.complete_text.setWordWrap(True); self.complete_text.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.complete_text.setMargin(25); self.viewer_stack.addWidget(self.complete_text)
        self.status=QLabel('Choose inputs, generate alternatives, and select a result.'); self.status.setWordWrap(True)
        self.status.setObjectName('muted'); center_layout.addWidget(self.status)
        center_layout.addWidget(QLabel('GENERATION HISTORY'))
        self.history=QListWidget(); self.history.setMaximumHeight(145); self.history.setIconSize(QSize(64,64))
        self.history.setFlow(QListWidget.Flow.LeftToRight); self.history.setWrapping(False)
        self.history.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        center_layout.addWidget(self.history)
        history_actions=QHBoxLayout(); center_layout.addLayout(history_actions)
        self.use_btn=button('Use this result',self.choose,True); history_actions.addWidget(self.use_btn)
        self.restore_btn=button('Restore this input chain',self.restore); history_actions.addWidget(self.restore_btn)
        self.resume_btn=button('Resume job',self.resume); history_actions.addWidget(self.resume_btn)
        self.attach_btn=button('Attach provider job ID',self.attach_remote); history_actions.addWidget(self.attach_btn)
        self.pause_btn=button('Pause monitoring',self.pause); history_actions.addWidget(self.pause_btn)
        self.details_btn=button('Details',self.details); history_actions.addWidget(self.details_btn)
        splitter.addWidget(center)
        inspector=QWidget(); il=QVBoxLayout(inspector); il.setContentsMargins(4,0,0,0)
        self.inspector_stack=QStackedWidget(); il.addWidget(self.inspector_stack,1)
        self.build_description()
        self.build_image_controls('preview')
        self.build_image_controls('turnaround')
        self.build_crop_controls()
        self.build_mesh_controls()
        self.build_texture_controls()
        self.build_bounds_controls()
        self.add_inspector(QLabel('Complete the selected chain to publish this revision to the asset library.'))
        self.generate_btn=button('Save description revision',self.generate,True); il.addWidget(self.generate_btn)
        self.import_btn=button('Import an existing result…',self.import_result); il.addWidget(self.import_btn)
        splitter.addWidget(inspector); splitter.setSizes([170,760,340]); inspector.setMinimumWidth(300); inspector.setMaximumWidth(450)
        footer=QHBoxLayout(); layout.addLayout(footer)
        self.selection_label=QLabel(); footer.addWidget(self.selection_label,1)
        footer.addWidget(button('Back',self.back)); self.continue_btn=button('Continue',self.next,True); footer.addWidget(self.continue_btn)
        self.draft_timer=QTimer(self); self.draft_timer.setSingleShot(True); self.draft_timer.timeout.connect(self.save_draft)
        self.bounds_timer=QTimer(self); self.bounds_timer.setSingleShot(True); self.bounds_timer.timeout.connect(self.preview_bounds)
        for field in (self.mesh_poly,self.padding,self.crop_size): field.valueChanged.connect(self.mark_draft)
        for field in (self.mesh_detail,self.texture_mode,self.texture_size): field.currentTextChanged.connect(self.mark_draft)
        self.texture_prompt.textChanged.connect(self.mark_draft)
        self.crop_table.itemChanged.connect(self.mark_draft)
        self.history.currentItemChanged.connect(self.history_changed)
        self.steps.currentRowChanged.connect(self.change_step)
        self.jobs.progress.connect(self.job_progress); self.jobs.finished.connect(self.job_finished)
        self.load_draft()
        self.steps.setCurrentRow(min(asset.data.get('last_step',0),7))

    def add_inspector(self,widget):
        scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(widget); self.inspector_stack.addWidget(scroll)

    def build_description(self):
        panel=QWidget(); form=QFormLayout(panel)
        self.name=QLineEdit(self.asset.data['name']); form.addRow('Asset name',self.name)
        self.preset=QComboBox(); self.preset.addItem('Custom block'); self.preset_values=presets(); self.preset.addItems(self.preset_values)
        form.addRow('Material preset',self.preset)
        self.description=QTextEdit(); self.description.setPlaceholderText('Describe material, color, sculpted shapes, top and underside…'); self.description.setMinimumHeight(230)
        form.addRow('Description',self.description)
        self.ref_list=QListWidget(); self.ref_list.setMinimumHeight(110); form.addRow('Source images (optional)',self.ref_list)
        form.addRow(button('Add source images…',self.add_references)); form.addRow(button('Remove selected reference',self.remove_reference))
        hint=QLabel('Astra will use this description and your references to generate the preview. Each accepted preview then anchors the four-view sheet.'); hint.setWordWrap(True); form.addRow(hint)
        self.preset.currentTextChanged.connect(self.apply_preset)
        self.name.textChanged.connect(self.mark_dirty); self.description.textChanged.connect(self.mark_dirty)
        self.add_inspector(panel)

    def build_image_controls(self,stage):
        panel=QWidget(); box=QVBoxLayout(panel)
        box.addWidget(QLabel('Generation prompt'))
        prompt=QTextEdit(); prompt.setMinimumHeight(330); setattr(self,stage+'_prompt',prompt); box.addWidget(prompt)
        box.addWidget(button('Reset from selected description',lambda checked=False,s=stage:self.reset_prompt(s)))
        edit=QCheckBox('Edit the highlighted generation'); setattr(self,stage+'_edit',edit); box.addWidget(edit)
        feedback=QTextEdit(); feedback.setMaximumHeight(100); feedback.setPlaceholderText('Optional feedback for this attempt…')
        setattr(self,stage+'_feedback',feedback); box.addWidget(feedback)
        text='Review the block style before selecting.' if stage=='preview' else 'Check shared faces and both undersides. View labels must not appear in the generated sheet.'
        hint=QLabel(text); hint.setWordWrap(True); box.addWidget(hint); box.addStretch()
        prompt.textChanged.connect(self.mark_draft); feedback.textChanged.connect(self.mark_draft)
        self.add_inspector(panel)

    def build_crop_controls(self):
        panel=QWidget(); box=QVBoxLayout(panel)
        hint=QLabel('Crop coordinates are in source-image pixels. All four crops share one scale and padding; pale pixels are preserved.'); hint.setWordWrap(True); box.addWidget(hint)
        self.crop_table=QTableWidget(4,4); self.crop_table.setHorizontalHeaderLabels(['Left','Top','Right','Bottom']); self.crop_table.setVerticalHeaderLabels([v.upper() for v in VIEWS])
        self.crop_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch); self.crop_table.setMaximumHeight(220); box.addWidget(self.crop_table)
        box.addWidget(button('Reset to four equal cells',self.reset_crops)); box.addWidget(button('Preview crops',self.preview_crops))
        form=QFormLayout(); box.addLayout(form)
        self.padding=QSpinBox(); self.padding.setRange(0,512); self.padding.setValue(16); form.addRow('Padding (pixels)',self.padding)
        self.crop_size=QSpinBox(); self.crop_size.setRange(256,4096); self.crop_size.setSingleStep(256); self.crop_size.setValue(1024); form.addRow('Output size',self.crop_size)
        box.addStretch(); self.add_inspector(panel)

    def build_mesh_controls(self):
        panel=QWidget(); form=QFormLayout(panel)
        self.mesh_poly=QSpinBox(); self.mesh_poly.setRange(100,300000); self.mesh_poly.setSingleStep(1000); self.mesh_poly.setValue(self.preferences.load()['polycount'])
        form.addRow('Target triangles',self.mesh_poly)
        self.mesh_detail=QComboBox(); self.mesh_detail.addItems(['standard','2k']); self.mesh_detail.setCurrentText(self.preferences.load()['geometry_resolution']); form.addRow('Geometry detail',self.mesh_detail)
        hint=QLabel('Generate an untextured mesh from the four selected crops. Orbit to inspect the silhouette and underside before texturing. Each rerun is a new paid provider task.'); hint.setWordWrap(True); form.addRow(hint)
        self.mesh_info=QLabel(); self.mesh_info.setWordWrap(True); form.addRow(self.mesh_info); self.add_inspector(panel)

    def build_texture_controls(self):
        panel=QWidget(); form=QFormLayout(panel)
        self.texture_mode=QComboBox(); self.texture_mode.addItems(['multiview','image','text']); self.texture_mode.setCurrentText(self.preferences.load()['texture_mode']); form.addRow('Guidance',self.texture_mode)
        self.texture_size=QComboBox(); self.texture_size.addItems(['2k','4k','8k']); self.texture_size.setCurrentText(self.preferences.load()['texture_resolution']); form.addRow('Texture resolution',self.texture_size)
        self.texture_prompt=QTextEdit(); self.texture_prompt.setPlaceholderText('Only used in text guidance mode (up to 800 characters).'); self.texture_prompt.setMinimumHeight(180); form.addRow('Texture description',self.texture_prompt)
        hint=QLabel('Multiview uses all four accepted crops. Image mode uses the main crop. Texture attempts stay linked to the selected geometry.'); hint.setWordWrap(True); form.addRow(hint); self.add_inspector(panel)

    def build_bounds_controls(self):
        panel=QWidget(); form=QFormLayout(panel)
        hint=QLabel('The mint box is the logical cell. Geometry may extend beyond it. Adjust its center, size and orientation, then inspect neighboring blocks.'); hint.setWordWrap(True); form.addRow(hint)
        self.bound_fields={}
        for key,label in [('cx','Center X'),('cy','Center Y'),('cz','Center Z'),('size','Cube side length'),('rx','Rotation X'),('ry','Rotation Y'),('rz','Rotation Z')]:
            field=QDoubleSpinBox(); field.setDecimals(5); field.setRange(.00001 if key=='size' else -100000,100000)
            field.setSingleStep(5 if key.startswith('r') else .025); field.setValue(1 if key=='size' else 0)
            field.valueChanged.connect(self.queue_bounds_preview); self.bound_fields[key]=field; form.addRow(label,field)
        form.addRow(button('Guess placement cube',self.guess_bounds))
        self.tile_check=QCheckBox('Show 3 × 3 neighboring blocks'); self.tile_check.toggled.connect(self.toggle_tiling); form.addRow(self.tile_check)
        self.bounds_info=QLabel(); self.bounds_info.setWordWrap(True); form.addRow(self.bounds_info)
        form.addRow(QLabel('Unit cube: −0.5 to +0.5 · Y up · center pivot'))
        self.add_inspector(panel)

    def mark_dirty(self,*args):
        if not self.loading:
            self._description_dirty=True; self.mark_draft()

    def mark_draft(self,*args):
        if not self.loading and hasattr(self,'draft_timer'): self.draft_timer.start(400)

    def save_draft(self):
        try: crop_rects=self.crop_rectangles()
        except (AttributeError,ValueError): crop_rects=None
        self.asset.data['draft']={'description':self.description.toPlainText(),'references':self.references,
                                 'preview_prompt':self.preview_prompt.toPlainText(),'turnaround_prompt':self.turnaround_prompt.toPlainText(),
                                 'preview_feedback':self.preview_feedback.toPlainText(),'turnaround_feedback':self.turnaround_feedback.toPlainText(),
                                 'crop_rectangles':crop_rects,'crop_source':str(self.crop_source.relative_to(self.asset.path)) if self.crop_source else None,
                                 'padding':self.padding.value(),'crop_size':self.crop_size.value(),
                                 'mesh_poly':self.mesh_poly.value(),'mesh_detail':self.mesh_detail.currentText(),
                                 'texture_mode':self.texture_mode.currentText(),'texture_size':self.texture_size.currentText(),
                                 'texture_prompt':self.texture_prompt.toPlainText(), 'bounds':self.bounds_spec(),
                                 'bounds_source':str(self.bounds_source.relative_to(self.asset.path)) if self.bounds_source else None}
        self.restored_draft=dict(self.asset.data['draft'])
        self.asset.data['name']=self.name.text().strip() or self.asset.data['name']
        self.title.setText(self.asset.data['name']); self.asset.save()

    def load_draft(self):
        self.loading=True
        draft=self.asset.data.get('draft',{})
        selected=self.asset.selected('description',False)
        spec=selected['spec'] if selected else {}
        self.description.setPlainText(draft.get('description',spec.get('description','')))
        self.references=draft.get('references',spec.get('references',[])); self.refresh_refs()
        for stage in ('preview','turnaround'):
            getattr(self,stage+'_prompt').setPlainText(draft.get(stage+'_prompt',''))
            getattr(self,stage+'_feedback').setPlainText(draft.get(stage+'_feedback',''))
        for key,field in (('mesh_poly',self.mesh_poly),('padding',self.padding),('crop_size',self.crop_size)):
            if key in draft: field.setValue(draft[key])
        for key,field in (('mesh_detail',self.mesh_detail),('texture_mode',self.texture_mode),('texture_size',self.texture_size)):
            if key in draft: field.setCurrentText(draft[key])
        self.texture_prompt.setPlainText(draft.get('texture_prompt',''))
        self.loading=False

    def settings(self):
        dialog=SettingsDialog(self.preferences,self.credentials,self)
        if dialog.exec(): self.jobs.pool.setMaxThreadCount(self.preferences.load()['concurrency'])

    def apply_preset(self,name):
        if name in self.preset_values:
            self.description.setPlainText(self.preset_values[name])
            if self.name.text() in ('New Block','Untitled block'): self.name.setText(name)

    def add_references(self):
        paths,_=QFileDialog.getOpenFileNames(self,'Source images','','Images (*.png *.jpg *.jpeg *.webp)')
        try:
            for path in paths: self.references.append(self.asset.add_source(path))
            self.refresh_refs(); self.mark_dirty()
        except Exception as exc: error(self,exc)

    def remove_reference(self):
        i=self.ref_list.currentRow()
        if i>=0: self.references.pop(i); self.refresh_refs(); self.mark_dirty()

    def refresh_refs(self):
        self.ref_list.clear()
        for ref in self.references: self.ref_list.addItem(ref['name'])
        if self.references: self.image_view.load(safe_child(self.asset.path,self.references[0]['path']))

    def reset_prompt(self,stage):
        try:
            description=self.asset.selected('description')['spec']['description']
            getattr(self,stage+'_prompt').setPlainText(preview_prompt(description) if stage=='preview' else turnaround_prompt(description))
        except Exception as exc: error(self,exc)

    def change_step(self,index):
        if index<0: return
        self.step=index; self.record=None; self.asset.data['last_step']=index; self.asset.save()
        self.stage_title.setText(f'{index+1:02d}  /  {LABELS[index]}')
        self.inspector_stack.setCurrentIndex(index)
        self.generate_btn.setText(['Save description revision','Generate preview','Generate four-view sheet','Save split revision','Generate mesh','Generate texture','Save bounds revision','Complete block'][index])
        self.generate_btn.setEnabled(True)
        self.import_btn.setVisible(index in (1,2,4,5))
        self.continue_btn.setVisible(index<7)
        self.history.setVisible(index<7)
        self.status.setText('Select a saved result to continue. New attempts do not replace your selection.')
        if index in (1,2) and not getattr(self,STAGES[index]+'_prompt').toPlainText().strip():
            if self.asset.selected('description',False): self.reset_prompt(STAGES[index])
        if index==3:
            self.viewer_stack.setCurrentIndex(2)
            sheet=self.asset.selected('turnaround',False)
            self.crop_source=self.asset.file(sheet,'image') if sheet else None
            if self.crop_source:
                if self.restored_draft.get('crop_source')==str(self.crop_source.relative_to(self.asset.path)) and self.restored_draft.get('crop_rectangles'):
                    self.set_crop_rectangles(self.restored_draft['crop_rectangles']); self.preview_crops()
                else: self.reset_crops()
        elif index in (4,5,6): self.viewer_stack.setCurrentIndex(1)
        elif index==7: self.viewer_stack.setCurrentIndex(3); self.refresh_completion()
        else: self.viewer_stack.setCurrentIndex(0)
        if index==5 and not self.texture_prompt.toPlainText():
            desc=self.asset.selected('description',False)
            if desc: self.texture_prompt.setPlainText(desc['spec']['description'][:800])
        if index==6:
            source=self.asset.selected('texture',False)
            self.bounds_source=self.asset.file(source,'model') if source else None
            if self.bounds_source:
                saved=self.asset.selected('bounds',False)
                if self.restored_draft.get('bounds_source')==str(self.bounds_source.relative_to(self.asset.path)) and self.restored_draft.get('bounds'):
                    self.set_bounds(self.restored_draft['bounds']); self.preview_bounds()
                elif saved: self.set_bounds(saved['spec'])
                else: self.guess_bounds()
        else:
            self.model_view.set_value('tiled',False); self.model_view.set_value('showBounds',False)
        self.refresh_history()
        # Keep uncommitted local adjustments when returning to a stage. Merely
        # highlighting its saved history must not discard that draft.
        if index==3 and self.crop_source and self.restored_draft.get('crop_source')==str(self.crop_source.relative_to(self.asset.path)) and self.restored_draft.get('crop_rectangles'):
            self.set_crop_rectangles(self.restored_draft['crop_rectangles']); self.preview_crops()
        if index==6 and self.bounds_source and self.restored_draft.get('bounds_source')==str(self.bounds_source.relative_to(self.asset.path)) and self.restored_draft.get('bounds'):
            self.set_bounds(self.restored_draft['bounds']); self.preview_bounds()

    def refresh_history(self,focus_id=None):
        self.history.blockSignals(True); self.history.clear()
        stage=STAGES[self.step] if self.step<7 else None
        chosen=None
        if stage:
            selected_id=self.asset.data['selections'].get(stage)
            for record in reversed(self.asset.attempts(stage)):
                current=record['id']==selected_id
                compatible=self.asset.compatible(record)
                text=('✓ ' if current else '')+record['created_at'][11:19]+'  '+record['status']
                if not compatible: text+='\nPrevious inputs'
                item=QListWidgetItem(text); item.setData(Qt.ItemDataRole.UserRole,record['id']); item.setToolTip(record.get('error') or record['id'])
                for key in ('image','thumbnail','main'):
                    if key in record['outputs']:
                        item.setIcon(QIcon(str(self.asset.file(record,key)))); break
                self.history.addItem(item)
                if record['id']==focus_id or (not focus_id and current): chosen=item
            if chosen is None and self.history.count(): chosen=self.history.item(0)
        self.history.blockSignals(False)
        self.history.setCurrentItem(chosen) if chosen else self.history_changed(None,None)
        self.update_steps()

    def update_steps(self):
        for i,stage in enumerate(STAGES):
            ok=self.asset.selected(stage,False)
            self.steps.item(i).setText(('✓  ' if ok else f'{i+1}.  ')+LABELS[i])
        if self.step<7:
            record=self.asset.selected(STAGES[self.step],False)
            self.selection_label.setText('Selected: '+record['id'] if record else 'No current selection for this stage')
        else: self.selection_label.setText('Finish to publish a new immutable revision.')

    def history_changed(self,item,previous):
        self.record=None
        if item and self.step<7:
            self.record=self.asset.get(STAGES[self.step],item.data(Qt.ItemDataRole.UserRole))
        record=self.record
        succeeded=bool(record and record['status']=='succeeded')
        self.use_btn.setEnabled(succeeded and self.asset.compatible(record))
        self.restore_btn.setEnabled(succeeded and not self.asset.compatible(record))
        self.resume_btn.setEnabled(bool(record and record['status']=='interrupted' and self.step in (1,2,4,5)))
        self.pause_btn.setEnabled(bool(record and record['id'] in self.jobs.workers))
        self.attach_btn.setEnabled(bool(record and record['status']=='submission_unknown'))
        self.details_btn.setEnabled(bool(record))
        if not record:
            if self.step in (1,2): self.image_view.load(None)
            if self.step in (4,5): self.model_view.load(None)
            return
        self.status.setText(record.get('error') or ('Selected for this stage.' if self.asset.data['selections'].get(record['stage'])==record['id'] else 'Review this attempt, then use it or generate another.'))
        if not succeeded: return
        if self.step in (1,2): self.image_view.load(self.asset.file(record,'image'))
        elif self.step in (4,5):
            path=self.asset.file(record,'model'); self.model_view.load(path)
            try:
                stats=self.geometry_cache.get(str(path)) or inspect(path); self.geometry_cache[str(path)]=stats
                self.mesh_info.setText(f"{stats['triangles']:,} triangles\nBounds: {stats['min']} to {stats['max']}")
            except Exception as exc: self.status.setText(str(exc))
        elif self.step==3:
            for view,name in zip(self.crop_views,VIEWS): view.load(self.asset.file(record,name))
            spec=record['spec']
            if 'rectangles' in spec:
                self.set_crop_rectangles(spec['rectangles']); self.padding.setValue(spec.get('padding',16)); self.crop_size.setValue(spec.get('output_size',1024))
        elif self.step==6:
            self.set_bounds(record['spec'])
            self.model_view.load(self.asset.file(record,'model'),unit=True,bounds=True)

    def choose(self):
        try:
            if not self.record: return
            self.asset.choose(self.record)
            if self.step==0:
                self.loading=True; self.description.setPlainText(self.record['spec']['description']); self.references=self.record['spec'].get('references',[]); self.refresh_refs(); self.loading=False
                self.preview_prompt.clear(); self.turnaround_prompt.clear(); self.save_draft(); self._description_dirty=False
            self.refresh_history(self.record['id'])
        except Exception as exc: error(self,exc)

    def restore(self):
        try:
            self.asset.choose(self.record,restore=True)
            self.change_step(self.step)
        except Exception as exc: error(self,exc)

    def generate(self):
        record=None
        try:
            self.save_draft()
            if self.step==7:
                self.publish(); return
            stage=STAGES[self.step]; settings=self.preferences.load()
            if self.step==0:
                text=self.description.toPlainText().strip()
                if not text: raise ValueError('Describe the block first.')
                record=self.asset.begin(stage,{'description':text,'references':self.references.copy()})
                folder=self.asset.attempt_dir(stage,record['id']); write_json(folder/'description.json',record['spec'])
                self.asset.succeed(record,{'description':'description.json'})
            elif self.step in (1,2,4,5):
                provider='openai' if self.step in (1,2) else 'meshy'
                if not self.credentials.get(provider): raise ValueError(f'Add your {provider} API key in Settings first.')
                spec={'template_version':1}
                if self.step in (1,2):
                    spec['prompt']=getattr(self,stage+'_prompt').toPlainText().strip()
                    if not spec['prompt']: raise ValueError('Enter a generation prompt.')
                    feedback=getattr(self,stage+'_feedback').toPlainText().strip()
                    if feedback: spec['prompt']+='\nRequested refinements: '+feedback
                    if getattr(self,stage+'_edit').isChecked():
                        if not self.record or self.record['status']!='succeeded': raise ValueError('Highlight a successful generation to edit.')
                        spec['edit_attempt']=self.record['id']
                        spec['prompt']='Edit the first supplied image using these instructions.\n'+spec['prompt']
                if self.step==4:
                    settings['polycount']=self.mesh_poly.value(); settings['geometry_resolution']=self.mesh_detail.currentText()
                if self.step==5:
                    settings['texture_mode']=self.texture_mode.currentText(); settings['texture_resolution']=self.texture_size.currentText()
                    spec['prompt']=self.texture_prompt.toPlainText()
                    if settings['texture_mode']=='multiview' and settings['texture_model']!='meshy-7': raise ValueError('Set the texture model to meshy-7 for multiview guidance.')
                record=self.asset.begin(stage,spec,settings)
                if 'prompt' in spec: atomic_bytes(self.asset.attempt_dir(stage,record['id'])/'prompt.txt',spec['prompt'].encode('utf-8'))
                self.jobs.start(self.asset,record)
            elif self.step==3:
                spec={'rectangles':self.crop_rectangles(),'padding':self.padding.value(),'output_size':self.crop_size.value()}
                record=self.asset.begin(stage,spec)
                folder=self.asset.attempt_dir(stage,record['id'])
                sheet=self.asset.file(self.asset.selected('turnaround'),'image')
                self.run_local(record,lambda:split_sheet(sheet,folder,**spec))
            elif self.step==6:
                if not self.bounds_source: raise ValueError('Select a texture result first.')
                spec=self.bounds_spec(); record=self.asset.begin(stage,spec)
                folder=self.asset.attempt_dir(stage,record['id'])
                source=self.bounds_source
                def save_bounds():
                    metadata=normalize_glb(source,folder/'model.glb',spec); write_json(folder/'bounds.json',metadata)
                    return {'model':'model.glb','bounds':'bounds.json'}
                self.run_local(record,save_bounds)
            if record: self.refresh_history(record['id'])
        except Exception as exc:
            if record and record['status']=='pending': self.asset.update(record,status='failed',error=str(exc)); self.refresh_history(record['id'])
            error(self,exc)

    def run_local(self,record,function):
        self.local_pending+=1
        self.asset.update(record,status='running')
        asset=self.asset
        def work():
            try:
                outputs=function(); asset.succeed(record,outputs)
            except Exception as exc:
                asset.update(record,status='failed',error=str(exc)); raise
            return record
        def finished(result,failure):
            self.local_pending-=1
            if self.step<7 and STAGES[self.step]==record['stage']: self.refresh_history(record['id'])
            if failure: error(self,failure)
        self.tasks.submit(work,finished)

    def import_result(self):
        stage=STAGES[self.step]
        pattern='Images (*.png *.jpg *.jpeg *.webp)' if self.step in (1,2) else 'Embedded glTF (*.glb)'
        path,_=QFileDialog.getOpenFileName(self,'Import '+stage,'',pattern)
        if not path: return
        record=None
        try:
            if self.step in (4,5): inspect(path)
            record=self.asset.begin(stage,{'imported_filename':Path(path).name,'imported_sha256':digest(path)})
            folder=self.asset.attempt_dir(stage,record['id'])
            if self.step in (1,2):
                shutil.copy2(path,folder/('original'+Path(path).suffix.lower()))
                with Image.open(path) as im: ImageOps.exif_transpose(im).convert('RGB').save(folder/'image.png')
                outputs={'image':'image.png','original':'original'+Path(path).suffix.lower()}
            else:
                shutil.copy2(path,folder/'model.glb'); outputs={'model':'model.glb'}
            self.asset.succeed(record,outputs); self.refresh_history(record['id'])
        except Exception as exc:
            if record: self.asset.update(record,status='failed',error=str(exc))
            error(self,exc)

    def next(self):
        try:
            self.asset.selected(STAGES[self.step])
            if self.step==0:
                selected=self.asset.selected('description')['spec']
                if self.description.toPlainText().strip()!=selected['description'] or self.references!=selected.get('references',[]):
                    raise ValueError('Your description has unsaved revisions. Save a revision and select it before continuing.')
            self.steps.setCurrentRow(min(7,self.step+1))
        except Exception as exc: error(self,exc)

    def back(self): self.steps.setCurrentRow(max(0,self.step-1))

    def resume(self):
        try: self.jobs.start(self.asset,self.record); self.refresh_history(self.record['id'])
        except Exception as exc: error(self,exc)

    def pause(self):
        if self.record: self.jobs.pause(self.record['id']); self.status.setText('Pausing local monitoring. The provider job may continue.')

    def attach_remote(self):
        if not self.record: return
        task_id,ok=QInputDialog.getText(self,'Recover remote task','Paste the existing provider job/response ID:')
        if ok and task_id:
            try:
                if not re.fullmatch(r'[A-Za-z0-9_-]+',task_id): raise ValueError('Invalid provider ID.')
                provider='openai' if self.step in (1,2) else 'meshy'
                endpoint='responses' if provider=='openai' else ('multi-image-to-3d' if self.step==4 else 'retexture')
                self.asset.update(self.record,remote={'provider':provider,'endpoint':endpoint,'id':task_id},status='interrupted')
                self.resume()
            except Exception as exc: error(self,exc)

    def details(self):
        if self.record:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.asset.attempt_dir(self.record['stage'],self.record['id']))))

    def job_progress(self,attempt_id,message):
        if self.record and self.record['id']==attempt_id: self.status.setText(message)

    def job_finished(self,attempt_id):
        if self.step<7 and any(a['id']==attempt_id for a in self.asset.attempts(STAGES[self.step])):
            self.refresh_history(attempt_id)

    def crop_rectangles(self):
        return [[int(self.crop_table.item(row,col).text()) for col in range(4)] for row in range(4)]

    def set_crop_rectangles(self,rectangles):
        for row,rect in enumerate(rectangles):
            for col,value in enumerate(rect): self.crop_table.setItem(row,col,QTableWidgetItem(str(value)))

    def reset_crops(self):
        try:
            if not self.crop_source: raise ValueError('Select a four-view sheet first.')
            with Image.open(self.crop_source) as image: rects=quadrants(image.size)
            self.set_crop_rectangles(rects); self.preview_crops()
        except Exception as exc: error(self,exc)

    def preview_crops(self):
        try:
            if not self.crop_source: raise ValueError('Select a four-view sheet first.')
            source=QPixmap(str(self.crop_source))
            for view,rect in zip(self.crop_views,self.crop_rectangles()):
                x0,y0,x1,y1=rect
                if not (0<=x0<x1<=source.width() and 0<=y0<y1<=source.height()): raise ValueError('Crop rectangle lies outside the source.')
                view.original=source.copy(x0,y0,x1-x0,y1-y0); view.fit()
            self.status.setText('Crop preview. Save a split revision and select it to use these views.')
        except Exception as exc: error(self,exc)

    def bounds_spec(self):
        return {'center':[self.bound_fields[k].value() for k in ('cx','cy','cz')],
                'size':self.bound_fields['size'].value(),
                'rotation':[self.bound_fields[k].value() for k in ('rx','ry','rz')]}

    def set_bounds(self,spec):
        self.loading=True
        for key,value in zip(('cx','cy','cz','size','rx','ry','rz'),[*spec['center'],spec['size'],*spec.get('rotation',[0,0,0])]):
            self.bound_fields[key].setValue(value)
        self.loading=False

    def guess_bounds(self):
        if not self.bounds_source:
            error(self,'Select a texture result first.'); return
        source=self.bounds_source
        version=self.bounds_edit_revision
        self.status.setText('Estimating the placement cube from surface samples…')
        def finished(spec,failure):
            if self.step!=6 or source!=self.bounds_source or version!=self.bounds_edit_revision: return
            if failure: self.status.setText(str(failure)); return
            self.set_bounds(spec); self.preview_bounds()
        self.tasks.submit(lambda:suggest_bounds(source),finished)

    def queue_bounds_preview(self,*args):
        if not self.loading and hasattr(self,'bounds_timer'):
            self.bounds_edit_revision+=1
            self.bounds_timer.start(250); self.mark_draft()

    def preview_bounds(self):
        if not self.bounds_source or self.step!=6: return
        self.preview_request+=1
        request=self.preview_request
        source=self.bounds_source; spec=self.bounds_spec()
        folder=self.asset.path/'.cache'; folder.mkdir(exist_ok=True)
        path=folder/(uid()+'.glb')
        def finished(metadata,failure):
            if self.step!=6 or source!=self.bounds_source or request!=self.preview_request:
                path.unlink(missing_ok=True); return
            if failure:
                self.status.setText(str(failure)); return
            self.bounds_cache.append(path); self.model_view.load(path,unit=True,bounds=True)
            self.model_view.set_value('tiled',self.tile_check.isChecked())
            self.bounds_info.setText('Overhang beyond unit cell:\nNegative XYZ: '+', '.join(f'{v:.3f}' for v in metadata['protrusion_negative'])+'\nPositive XYZ: '+', '.join(f'{v:.3f}' for v in metadata['protrusion_positive']))
            while len(self.bounds_cache)>3:
                old=self.bounds_cache.pop(0)
                try: old.unlink(missing_ok=True)
                except OSError: pass
        self.tasks.submit(lambda:normalize_glb(source,path,spec),finished)

    def toggle_tiling(self,value):
        self.model_view.set_value('tiled',value); self.model_view.set_value('distance',6.0 if value else 3.6)

    def refresh_completion(self):
        lines=['Ready to complete this block\n','The selected chain will be saved as a new immutable library revision.\n']
        valid=True
        for stage in STAGES:
            selected=self.asset.selected(stage,False)
            lines.append(('✓ ' if selected else '○ ')+stage.title()+': '+(selected['id'] if selected else 'select a current result'))
            valid=valid and selected is not None
        self.complete_text.setText('\n\n'.join(lines)); self.generate_btn.setEnabled(valid)

    def publish(self):
        folder=self.asset.complete()
        self.status.setText('Completed: '+str(folder)); self.accept()

    def done(self,result):
        if self.local_pending:
            error(self,'A local revision is still being saved. Wait for it to finish before closing this block.')
            return
        self.draft_timer.stop(); self.bounds_timer.stop(); self.save_draft()
        try:
            self.jobs.progress.disconnect(self.job_progress); self.jobs.finished.disconnect(self.job_finished)
        except RuntimeError: pass
        super().done(result)
