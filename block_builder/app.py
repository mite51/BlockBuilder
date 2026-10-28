from __future__ import annotations
import argparse
import sys
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication,QMessageBox
from .config import Preferences,Credentials,PROJECT
from .jobs import JobManager
from .ui.library import MainWindow

STYLE = '''
QWidget { background:#101722; color:#dfe7f3; font-family:Segoe UI; font-size:13px; }
QMainWindow,QDialog { background:#101722; }
QLabel { background:transparent; }
QLabel#hero { font-size:36px; font-weight:700; color:#f4f8ff; }
QLabel#title { font-size:24px; font-weight:650; }
QLabel#subtitle { font-size:18px; font-weight:600; padding:6px 0; }
QLabel#eyebrow { color:#64d9bf; font-size:11px; font-weight:700; letter-spacing:2px; }
QLabel#muted { color:#96a7bd; }
QLabel#cardTitle { font-size:16px; font-weight:600; padding-top:5px; }
QLabel#plus { color:#64d9bf; font-size:64px; font-weight:300; }
QFrame#assetCard { background:#1a2432; border:1px solid #2a3749; border-radius:12px; }
QFrame#createCard { background:#142b2a; border:1px dashed #3b8e7e; border-radius:12px; }
QPushButton { background:#243246; border:1px solid #36465d; border-radius:6px; padding:9px 13px; color:#e8edf6; }
QPushButton:hover { background:#31445e; border-color:#5b7898; }
QPushButton:pressed { background:#162536; }
QPushButton:disabled { color:#617086; background:#1b2635; border-color:#243246; }
QPushButton#primary { background:#4cd1b0; color:#092a24; border-color:#4cd1b0; font-weight:650; }
QPushButton#primary:hover { background:#71e1c4; }
QPushButton#primary:disabled { background:#23483f; color:#68877f; border-color:#23483f; }
QLineEdit,QTextEdit,QSpinBox,QDoubleSpinBox,QComboBox { background:#182232; border:1px solid #344359; border-radius:5px; padding:7px; selection-background-color:#286b61; }
QLineEdit:focus,QTextEdit:focus { border-color:#4cd1b0; }
QComboBox QAbstractItemView { background:#182232; selection-background-color:#286b61; }
QListWidget,QTableWidget { background:#141e2c; border:1px solid #29374b; border-radius:6px; padding:4px; }
QListWidget::item { padding:9px; border-radius:4px; }
QListWidget::item:selected { background:#254b49; color:#a8f5e2; }
QListWidget::item:hover { background:#243447; }
QHeaderView::section { background:#243246; padding:5px; border:none; color:#bac8db; }
QScrollArea { background:transparent; border:none; }
QScrollBar:vertical { background:#141e2c; width:10px; }
QScrollBar::handle:vertical { background:#3a4c65; min-height:30px; border-radius:4px; }
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical { height:0; }
QSplitter::handle { background:#263448; width:2px; }
QTabWidget::pane { border:1px solid #29374b; border-radius:6px; }
QTabBar::tab { background:#1a2535; padding:10px 18px; }
QTabBar::tab:selected { background:#2b4b4b; color:#8debd2; }
QCheckBox { spacing:7px; padding:4px; }
QToolTip { background:#26374b; color:#e6edf7; border:1px solid #526781; }
'''


def main(argv=None):
    parser=argparse.ArgumentParser(description='Block Builder — versioned terrain asset authoring')
    parser.add_argument('--library',type=Path,help='Open this asset library without changing saved preferences')
    parser.add_argument('--demo',action='store_true',help='Create and open a separate offline demonstration library')
    parser.add_argument('--smoke-test',action='store_true',help='Open briefly, save a screenshot, and exit')
    args=parser.parse_args(argv)
    app=QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName('Block Builder'); app.setOrganizationName('TerraceWorld')
    app.setFont(QFont('Segoe UI',10)); app.setStyle('Fusion'); app.setStyleSheet(STYLE)
    preferences=Preferences(); credentials=Credentials()
    library=args.library
    if args.demo:
        from .demo import make_demo
        library=library or PROJECT/'output'/'block_builder_demo'
        make_demo(library)
    jobs=JobManager(credentials,preferences.load()['concurrency'])
    try:
        window=MainWindow(preferences,credentials,jobs,library)
    except Exception as exc:
        if args.smoke_test: print('Startup failed:',str(exc),flush=True)
        else: QMessageBox.critical(None,'Block Builder could not open',str(exc))
        return 1
    window.show()
    if args.smoke_test:
        folder=PROJECT/'output'/'block_builder_checks'; folder.mkdir(parents=True,exist_ok=True)
        def finish():
            window.grab().save(str(folder/'library.png'))
            errors=[]
            for card in window.cards:
                if getattr(card,'viewer',None): errors.extend(e.toString() for e in card.viewer.errors())
            print('UI smoke test:', 'PASS' if not errors else errors, flush=True)
            app.exit(1 if errors else 0)
        QTimer.singleShot(3500,finish)
    return app.exec()
