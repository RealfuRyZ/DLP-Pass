"""Qt 绑定兼容层：同一套界面代码同时支持 PyQt6 与 PyQt5。

- 现代版发布构建（Windows 10/11）：优先加载 **PyQt6**；
- Win7 兼容版发布构建（Windows 7 SP1+）：回退加载 **PyQt5**。

PyQt5 5.15 与 PyQt6 均支持作用域枚举写法（如 ``Qt.AlignmentFlag.AlignCenter``、
``QHeaderView.ResizeMode.Stretch``），因此各界面模块无需任何分叉代码，
只通过本模块导入所用到的 Qt 名字即可。
"""

from __future__ import annotations

try:
    from PyQt6 import QtCore, QtGui, QtWidgets  # type: ignore[import-not-found]

    QT_BINDING = "PyQt6"
except ImportError:  # pragma: no cover - Windows 7 兼容版环境
    from PyQt5 import QtCore, QtGui, QtWidgets  # type: ignore[import-not-found]

    QT_BINDING = "PyQt5"

IS_QT6 = QT_BINDING == "PyQt6"

# ---------------------------------------------------------------- QtCore
Qt = QtCore.Qt
QObject = QtCore.QObject
QTimer = QtCore.QTimer
QThread = QtCore.QThread
QUrl = QtCore.QUrl
pyqtSignal = QtCore.pyqtSignal

# ---------------------------------------------------------------- QtGui
QColor = QtGui.QColor
QDesktopServices = QtGui.QDesktopServices
QFont = QtGui.QFont
QIcon = QtGui.QIcon
QPainter = QtGui.QPainter
QPen = QtGui.QPen
QPixmap = QtGui.QPixmap

# ------------------------------------------------------------- QtWidgets
QAbstractItemView = QtWidgets.QAbstractItemView
QApplication = QtWidgets.QApplication
QButtonGroup = QtWidgets.QButtonGroup
QCheckBox = QtWidgets.QCheckBox
QFileDialog = QtWidgets.QFileDialog
QFrame = QtWidgets.QFrame
QGraphicsDropShadowEffect = QtWidgets.QGraphicsDropShadowEffect
QHBoxLayout = QtWidgets.QHBoxLayout
QHeaderView = QtWidgets.QHeaderView
QLabel = QtWidgets.QLabel
QLineEdit = QtWidgets.QLineEdit
QMainWindow = QtWidgets.QMainWindow
QMessageBox = QtWidgets.QMessageBox
QProgressBar = QtWidgets.QProgressBar
QProgressDialog = QtWidgets.QProgressDialog
QPushButton = QtWidgets.QPushButton
QRadioButton = QtWidgets.QRadioButton
QSizePolicy = QtWidgets.QSizePolicy
QSplitter = QtWidgets.QSplitter
QTableWidget = QtWidgets.QTableWidget
QTableWidgetItem = QtWidgets.QTableWidgetItem
QTextEdit = QtWidgets.QTextEdit
QVBoxLayout = QtWidgets.QVBoxLayout
QWidget = QtWidgets.QWidget


def prepare_high_dpi() -> None:
    """在创建 QApplication 之前设置高分屏策略（两版本行为一致）。"""
    if IS_QT6:
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
        return
    # PyQt5 需要显式开启高 DPI 缩放与高清位图（在 Win7 上无副作用）
    try:
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    except AttributeError:
        pass
