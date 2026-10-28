from pathlib import Path
from PySide6.QtCore import Qt,QTimer,QUrl,QPoint,QLockFile
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QScrollArea,
    QLabel,QLineEdit,QInputDialog,QFrame,QMessageBox,QStackedWidget)
from ..storage import Library,STAGES,read_json
from .common import button,error,BlockView,ImageView
from .settings import SettingsDialog
from .wizard import Wizard


class AssetCard(QFrame):
    def __init__(self,asset,open_callback,preferences):
        super().__init__()
        self.setObjectName('assetCard'); self.setMinimumSize(265,300); self.setMaximumHeight(355)
        self.asset,self.preferences = asset,preferences
        self.viewer=None
        self.model=None
        self.unit=False
        box=QVBoxLayout(self); box.setContentsMargins(10,10,10,10)
        self.stack=QStackedWidget(); self.stack.setMinimumHeight(205)
        self.placeholder=ImageView(); self.stack.addWidget(self.placeholder); box.addWidget(self.stack,1)
        revision=asset.data.get('completed_revision')
        if revision:
            folder=asset.path/'completed'/revision
            self.model=folder/'block.glb'; self.unit=True
            self.placeholder.load(folder/'thumbnail.png')
            status='Complete'
            try:
                if read_json(folder/'selection.json')['selections']!=asset.data['selections']: status='Complete · New draft'
            except (OSError,ValueError): pass
        else:
            status='Draft'
            for stage in ('bounds','texture','mesh'):
                selected=asset.selected(stage,False)
                if selected:
                    self.model=asset.file(selected,'model'); self.unit=stage=='bounds'; status='Draft · '+stage.title(); break
            preview=asset.selected('preview',False)
            if preview: self.placeholder.load(asset.file(preview,'image'))
            else: self.placeholder.setText('Draft — resume the wizard')
        title=QLabel(asset.data['name']); title.setObjectName('cardTitle'); box.addWidget(title)
        row=QHBoxLayout(); row.addWidget(QLabel(status),1); row.addWidget(button('Open block',lambda:open_callback(asset))); box.addLayout(row)

    def activate(self,visible):
        if visible and self.model and self.viewer is None:
            self.viewer=BlockView(); self.stack.addWidget(self.viewer); self.stack.setCurrentWidget(self.viewer)
            self.viewer.load(self.model,unit=self.unit)
            self.viewer.set_value('spinning',self.preferences.load()['auto_rotate'])
            self.viewer.set_value('highQuality',self.preferences.load()['preview_quality']=='high')
        elif not visible and self.viewer:
            self.stack.setCurrentWidget(self.placeholder); self.stack.removeWidget(self.viewer)
            self.viewer.set_value('modelSource',QUrl()); self.viewer.deleteLater(); self.viewer=None


class MainWindow(QMainWindow):
    def __init__(self,preferences,credentials,jobs,library_override=None):
        super().__init__()
        self.preferences,self.credentials,self.jobs = preferences,credentials,jobs
        self.library_override=library_override
        self.library=Library(library_override or preferences.load()['library'])
        self.library_locks={}
        self.lock_library(self.library.root)
        self.cards=[]; self.columns=0; self.closing=False
        self.setWindowTitle('Block Builder'); self.resize(1280,860); self.setMinimumSize(850,600)
        base=QWidget(); self.setCentralWidget(base); outer=QVBoxLayout(base); outer.setContentsMargins(28,24,28,20); outer.setSpacing(16)
        header=QHBoxLayout(); outer.addLayout(header)
        titles=QVBoxLayout(); eyebrow=QLabel('TERRAIN ASSET STUDIO'); eyebrow.setObjectName('eyebrow'); titles.addWidget(eyebrow)
        title=QLabel('Block Builder'); title.setObjectName('hero'); titles.addWidget(title)
        subtitle=QLabel('Describe. Generate. Refine. Build your block library.'); subtitle.setObjectName('muted'); titles.addWidget(subtitle)
        header.addLayout(titles,1); header.addWidget(button('Open library folder',self.open_folder)); header.addWidget(button('Settings',self.settings))
        toolbar=QHBoxLayout(); outer.addLayout(toolbar)
        self.search=QLineEdit(); self.search.setPlaceholderText('Search blocks…'); self.search.setMaximumWidth(350); toolbar.addWidget(self.search)
        self.count=QLabel(); self.count.setObjectName('muted'); toolbar.addWidget(self.count,1)
        toolbar.addWidget(button('Rescan library',self.refresh))
        self.scroll=QScrollArea(); self.scroll.setWidgetResizable(True); self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.grid_widget=QWidget(); self.grid=QGridLayout(self.grid_widget); self.grid.setSpacing(18); self.grid.setContentsMargins(0,0,0,0)
        self.scroll.setWidget(self.grid_widget); outer.addWidget(self.scroll,1)
        self.notice=QLabel(); self.notice.setWordWrap(True); self.notice.setObjectName('muted'); outer.addWidget(self.notice)
        bottom=QHBoxLayout(); outer.addLayout(bottom); self.path_label=QLabel(); self.path_label.setObjectName('muted'); bottom.addWidget(self.path_label,1)
        self.job_label=QLabel('No active jobs'); self.job_label.setObjectName('muted'); bottom.addWidget(self.job_label)
        self.lazy_timer=QTimer(self); self.lazy_timer.setSingleShot(True); self.lazy_timer.timeout.connect(self.visibility)
        self.poll_timer=QTimer(self); self.poll_timer.setInterval(500); self.poll_timer.timeout.connect(self.update_jobs); self.poll_timer.start()
        self.scroll.verticalScrollBar().valueChanged.connect(lambda:self.lazy_timer.start(80))
        self.search.textChanged.connect(self.filter_cards)
        self.jobs.finished.connect(self.on_job_finished)
        recovered=[]
        for asset in self.library.scan(): recovered.extend(asset.recover())
        self.refresh()
        QTimer.singleShot(0,lambda:self.reflow(force=True))
        if recovered: self.notice.setText(f'{len(recovered)} interrupted attempt(s) found. Open the corresponding block to resume or reconcile its provider job.')

    def refresh(self):
        try:
            assets=self.library.scan()
            for card in self.cards:
                self.grid.removeWidget(card); card.deleteLater()
            self.cards=[]
            create=QFrame(); create.setObjectName('createCard'); create.setMinimumSize(265,300); create.setMaximumHeight(355)
            box=QVBoxLayout(create); box.setAlignment(Qt.AlignmentFlag.AlignCenter)
            plus=QLabel('+'); plus.setObjectName('plus'); plus.setAlignment(Qt.AlignmentFlag.AlignCenter); box.addWidget(plus)
            box.addWidget(button('Create new block',self.create,True))
            tip=QLabel('Start with an idea or a reference'); tip.setAlignment(Qt.AlignmentFlag.AlignCenter); tip.setObjectName('muted'); box.addWidget(tip)
            self.cards.append(create)
            for asset in sorted(assets,key=lambda a:a.data['name'].casefold()): self.cards.append(AssetCard(asset,self.open_asset,self.preferences))
            self.path_label.setText(str(self.library.root)); self.count.setText(f'{len(assets)} block'+('s' if len(assets)!=1 else ''))
            self.notice.setText('\n'.join(self.library.errors) if self.library.errors else ('Your library is ready. Add API keys in Settings, then create your first block.' if not assets else 'Each visible model has an independent 3D view. Drag to orbit; scroll to zoom.'))
            self.filter_cards()
        except Exception as exc: error(self,exc)

    def filter_cards(self,*args):
        self.reflow(force=True)

    def reflow(self,force=False):
        columns=max(1,self.scroll.viewport().width()//290)
        if not force and columns==self.columns: return
        self.columns=columns
        while self.grid.count(): self.grid.takeAt(0)
        query=self.search.text().strip().casefold(); visible=[]
        for i,card in enumerate(self.cards):
            match=i==0 or query in card.asset.data['name'].casefold()
            card.setVisible(match)
            if match: visible.append(card)
            elif isinstance(card,AssetCard): card.activate(False)
        for i,card in enumerate(visible): self.grid.addWidget(card,i//columns,i%columns)
        for col in range(12): self.grid.setColumnStretch(col,1 if col<columns else 0)
        for row in range(self.grid.rowCount()+1): self.grid.setRowStretch(row,0)
        self.grid.setRowStretch((len(visible)+columns-1)//columns,1)
        self.lazy_timer.start(100)

    def visibility(self):
        viewport=self.scroll.viewport(); rect=viewport.rect()
        for card in self.cards:
            if isinstance(card,AssetCard):
                top=card.mapTo(viewport,QPoint(0,0))
                visible=card.isVisible() and top.y()<rect.height() and top.y()+card.height()>0
                card.activate(visible)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'scroll'): QTimer.singleShot(0,self.reflow)

    def create(self):
        name,ok=QInputDialog.getText(self,'Create new block','Asset name:',text='New Block')
        if ok and name.strip():
            try: self.open_asset(self.library.create(name))
            except Exception as exc: error(self,exc)

    def open_asset(self,asset):
        for card in self.cards:
            if isinstance(card,AssetCard): card.activate(False)
        dialog=Wizard(asset,self.preferences,self.credentials,self.jobs,self); dialog.exec(); dialog.deleteLater()
        self.refresh()

    def settings(self):
        dialog=SettingsDialog(self.preferences,self.credentials,self)
        if dialog.exec():
            settings=self.preferences.load(); self.jobs.pool.setMaxThreadCount(settings['concurrency'])
            root=self.library_override or settings['library']
            if Path(root).resolve()!=self.library.root:
                try:
                    self.lock_library(Path(root).resolve())
                    self.library=Library(root)
                    for asset in self.library.scan(): asset.recover()
                except Exception as exc:
                    error(self,exc); return
            self.refresh()

    def lock_library(self,root):
        root=Path(root).resolve()
        if str(root) in self.library_locks: return
        root.mkdir(parents=True,exist_ok=True)
        lock=QLockFile(str(root/'.block_builder.lock'))
        lock.setStaleLockTime(0)
        if not lock.tryLock(1):
            raise ValueError('This library is already open in another Block Builder window, or is not writable. Close the other window or choose another library folder.')
        self.library_locks[str(root)]=lock

    def open_folder(self): QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.library.root)))

    def update_jobs(self):
        count=len(self.jobs.workers)
        self.job_label.setText(f'{count} active generation job'+('s' if count!=1 else '') if count else 'No active jobs')
        if self.closing and not count: self.close()

    def on_job_finished(self,attempt_id):
        self.update_jobs()

    def closeEvent(self,event):
        if self.jobs.workers:
            if not self.closing:
                self.closing=True; self.jobs.pause_all()
                self.notice.setText('Saving job state and pausing monitoring before closing. Remote jobs can continue; resume them next time.')
            event.ignore(); return
        self.poll_timer.stop(); super().closeEvent(event)
        for lock in self.library_locks.values(): lock.unlock()
