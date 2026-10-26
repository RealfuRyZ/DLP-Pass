"""自定义控件：卡片、拖拽区、状态徽标、统计卡、日志面板、加载动画。"""

from __future__ import annotations

import datetime
import html

from . import theme
from .qt_compat import (
    QColor,
    QFrame,
    QGraphicsDropShadowEffect,
    QLabel,
    QSizePolicy,
    Qt,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    pyqtSignal,
)


def format_size(num_bytes: int | None) -> str:
    """把字节数格式化为易读文本。"""
    if num_bytes is None:
        return "-"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return "-"


def apply_shadow(widget: QWidget, blur: int = 26, alpha: int = 80, y_offset: int = 6) -> None:
    """为控件添加柔和的投影效果（微阴影）。"""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, y_offset)
    effect.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(effect)


class Card(QFrame):
    """圆角卡片容器，可选标题。"""

    def __init__(self, title: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Card")
        apply_shadow(self)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 14, 16, 16)
        self._layout.setSpacing(10)
        if title:
            title_label = QLabel(title)
            title_label.setObjectName("CardTitle")
            self._layout.addWidget(title_label)

    def body(self) -> QVBoxLayout:
        """返回卡片内部布局，供调用方继续添加控件。"""
        return self._layout


class StatusBadge(QLabel):
    """胶囊状状态徽标（Badge），通过动态属性驱动 QSS 配色。

    state 取值：idle / accent / info / success / warning / danger
    """

    def __init__(self, text: str = "", state: str = "idle", parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setObjectName("Badge")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_state(state, text)

    def set_state(self, state: str, text: str | None = None) -> None:
        if text is not None:
            self.setText(text)
        self.setProperty("state", state)
        self.style().unpolish(self)
        self.style().polish(self)


class DropZone(QFrame):
    """拖拽区域：支持文件 / 文件夹拖入，点击触发手动选择。"""

    paths_dropped = pyqtSignal(list)
    clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(128)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(4)

        icon = QLabel("↓")
        icon.setObjectName("DropIcon")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("将文件或文件夹拖拽到此处")
        title.setObjectName("DropTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint = QLabel("支持批量拖入 · 自动识别加密文件 · 点击可手动选择")
        hint.setObjectName("DropHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(icon)
        layout.addWidget(title)
        layout.addWidget(hint)

    # ------------------------------------------------------------- 事件

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._set_active(True)

    def dragLeaveEvent(self, event):
        self._set_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._set_active(False)
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.paths_dropped.emit(paths)
        event.acceptProposedAction()

    def _set_active(self, active: bool) -> None:
        self.setProperty("active", "true" if active else "false")
        self.style().unpolish(self)
        self.style().polish(self)


class StatCard(QFrame):
    """统计卡片：大数字 + 说明文字。"""

    def __init__(self, caption: str, color: str | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("StatCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(0)

        self._value = QLabel("0")
        self._value.setObjectName("StatValue")
        if color:
            self._value.setStyleSheet(f"color: {color};")
        caption_label = QLabel(caption)
        caption_label.setObjectName("StatCaption")

        layout.addWidget(self._value)
        layout.addWidget(caption_label)

    def set_value(self, value: int | str) -> None:
        self._value.setText(str(value))

    def set_color(self, color: str) -> None:
        """更新数值文本颜色（主题切换时按当前配色重新着色）。"""
        self._value.setStyleSheet(f"color: {color};")


class LogPanel(QTextEdit):
    """实时日志面板：按级别着色高亮，自动滚动并限制最大行数。

    颜色在写入每条日志时从 :data:`theme.COLORS` 现取，主题切换后新日志自动
    使用当前配色（已写入的历史行保持原色）。
    """

    _LEVEL_COLOR_KEYS = {
        "info": "text",
        "success": "success",
        "warning": "warning",
        "error": "danger",
        "debug": "text_faint",
    }

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("LogView")
        self.setReadOnly(True)
        self.setAcceptRichText(True)
        self.document().setMaximumBlockCount(3000)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def add_entry(self, message: str, level: str = "info") -> None:
        color = theme.COLORS[self._LEVEL_COLOR_KEYS.get(level, "info")]
        faint = theme.COLORS["text_faint"]
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        safe = html.escape(message).replace("\n", "<br>")
        self.append(
            f'<span style="color:{faint};">[{timestamp}]</span> '
            f'<span style="color:{color};">{safe}</span>'
        )
        scrollbar = self.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def clear_log(self) -> None:
        self.clear()
