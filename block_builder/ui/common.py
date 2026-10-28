from pathlib import Path
from PySide6.QtCore import Qt, QUrl, QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QMessageBox, QPushButton
from PySide6.QtQuickWidgets import QQuickWidget


def button(text, callback=None, primary=False):
    widget = QPushButton(text)
    if primary:
        widget.setObjectName('primary')
    if callback:
        widget.clicked.connect(callback)
    return widget


def error(parent, exc):
    QMessageBox.warning(parent,'Block Builder',str(exc))


class ImageView(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.original = QPixmap()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(200,180)
        self.setStyleSheet('background:#171e28;border-radius:8px;color:#8d9eb5;')
        self.setText('Your image will appear here')

    def load(self, path):
        self.original = QPixmap(str(path)) if path else QPixmap()
        self.fit()

    def fit(self):
        if self.original.isNull():
            self.clear()
            self.setText('Your image will appear here')
        else:
            self.setPixmap(self.original.scaled(self.size(),Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit()


class BlockView(QQuickWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        self.setMinimumSize(180,170)
        self.setSource(QUrl.fromLocalFile(str(Path(__file__).with_name('BlockView.qml'))))
        self.model_path = None

    def set_value(self, key, value):
        if self.rootObject():
            self.rootObject().setProperty(key,value)

    def load(self, path, unit=False, bounds=False):
        self.model_path = Path(path) if path else None
        self.set_value('unitMode',unit)
        self.set_value('showBounds',bounds)
        self.set_value('modelSource',QUrl.fromLocalFile(str(Path(path).resolve())) if path else QUrl())

    def preset(self, name):
        if self.rootObject():
            self.rootObject().cameraPreset(name)

    def screenshot(self, path):
        self.grabFramebuffer().save(str(path))


class CallSignals(QObject):
    result = Signal(str,object,object)


class Call(QRunnable):
    def __init__(self, identifier, function):
        super().__init__()
        self.identifier,self.function=identifier,function
        self.signals=CallSignals()

    def run(self):
        try: result,failure=self.function(),None
        except Exception as exc: result,failure=None,exc
        self.signals.result.emit(self.identifier,result,failure)


class Tasks(QObject):
    """CPU/file work with callbacks delivered on the GUI thread."""
    def __init__(self,parent=None):
        super().__init__(parent)
        self.calls={}

    def submit(self,function,callback):
        from ..storage import uid
        identifier=uid(); call=Call(identifier,function)
        self.calls[identifier]=(call,callback)
        call.signals.result.connect(self.receive)
        QThreadPool.globalInstance().start(call)
        return identifier

    @Slot(str,object,object)
    def receive(self,identifier,result,failure):
        item=self.calls.pop(identifier,None)
        if item: item[1](result,failure)
