"""Qt 主题：暗色 / 亮色双调色板与全局样式表（QSS）。

设计目标：深灰 / 暗蓝（#1E1E1E 系）与浅灰白（#F4F5F7 系）两套配色共用同一份
模板化 QSS，仅替换颜色变量；切换主题后整个界面（背景、文字、边框、徽标等
全部视觉元素）随之更新。品牌蓝强调色、统一圆角、微阴影、悬停反馈与状态徽标
（Badge）风格在两套配色下保持一致。

用法：
- :func:`set_active_theme` 切换当前配色（``"dark"`` / ``"light"``），原地更新
  :data:`COLORS` 与 :data:`STATE_COLORS`，调用方持有的模块引用随之生效；
- :func:`build_stylesheet` 按当前配色生成全局样式表，配合
  ``QApplication.setStyleSheet`` 应用；
- 用户偏好持久化见 :mod:`dlp_pass.ui_qt.settings`。
"""

from __future__ import annotations

from string import Template

# 两套调色板必须保持相同的键集合：QSS 模板与运行时取色都依赖这些键。
DARK_COLORS = {
    "window": "#1E1E1E",
    "panel": "#26282D",
    "panel_alt": "#2B2E34",
    "elevated": "#31353C",
    "border": "#3A3F47",
    "border_soft": "#2F333A",
    "border_strong": "#4A505A",
    "pressed": "#24272C",
    "table_alt": "#24262B",
    "track": "#2A2D33",
    "scroll_handle": "#43474F",
    "scroll_handle_hover": "#555C66",
    "drop_soft": "rgba(76, 141, 255, 0.04)",
    "text": "#E8EAED",
    "text_muted": "#9BA1AB",
    "text_faint": "#6E747E",
    "accent": "#4C8DFF",
    "accent_hover": "#6DA3FF",
    "accent_pressed": "#3C78E6",
    "accent_soft": "rgba(76, 141, 255, 0.16)",
    "accent_disabled": "#3A4A66",
    "accent_disabled_text": "#A9B6CC",
    "success": "#3FBF7F",
    "success_soft": "rgba(63, 191, 127, 0.16)",
    "danger": "#E5646C",
    "danger_soft": "rgba(229, 100, 108, 0.16)",
    "warning": "#E0A83E",
    "warning_soft": "rgba(224, 168, 62, 0.16)",
    "info": "#56A6F0",
    "info_soft": "rgba(86, 166, 240, 0.16)",
}

LIGHT_COLORS = {
    "window": "#F4F5F7",
    "panel": "#FFFFFF",
    "panel_alt": "#EEF0F4",
    "elevated": "#E6E8EE",
    "border": "#D2D6DE",
    "border_soft": "#E3E6EB",
    "border_strong": "#BCC3CF",
    "pressed": "#D5D9E1",
    "table_alt": "#F2F3F7",
    "track": "#DDE1E8",
    "scroll_handle": "#C4C9D3",
    "scroll_handle_hover": "#A6ADBB",
    "drop_soft": "rgba(44, 111, 232, 0.06)",
    "text": "#1F2328",
    "text_muted": "#5A626E",
    "text_faint": "#8A919D",
    "accent": "#2C6FE8",
    "accent_hover": "#1E5FD2",
    "accent_pressed": "#1849AB",
    "accent_soft": "rgba(44, 111, 232, 0.12)",
    "accent_disabled": "#C3CFE6",
    "accent_disabled_text": "#8B97B0",
    "success": "#1E9E5E",
    "success_soft": "rgba(30, 158, 94, 0.14)",
    "danger": "#D6454F",
    "danger_soft": "rgba(214, 69, 79, 0.13)",
    "warning": "#B8791A",
    "warning_soft": "rgba(184, 121, 26, 0.15)",
    "info": "#2B7FD4",
    "info_soft": "rgba(43, 127, 212, 0.14)",
}

VALID_THEMES = ("dark", "light")

# 当前生效调色板：模块级字典原地更新，持有 theme.COLORS 引用的调用方即时生效
COLORS = dict(DARK_COLORS)

FONT_FAMILY = "Microsoft YaHei UI"

# 状态徽标 state 取值 -> 文本颜色（大批量列表中用纯文本状态替代徽标控件时使用）
STATE_COLORS = {}

_active_theme = "dark"


def _apply_palette(palette: dict) -> None:
    """把 ``palette`` 应用到当前配色，并重建派生的状态色表。"""
    COLORS.clear()
    COLORS.update(palette)
    STATE_COLORS.clear()
    STATE_COLORS.update(
        {
            "idle": COLORS["text_muted"],
            "accent": COLORS["accent"],
            "info": COLORS["info"],
            "success": COLORS["success"],
            "warning": COLORS["warning"],
            "danger": COLORS["danger"],
        }
    )


def active_theme() -> str:
    """当前主题名：``"dark"`` 或 ``"light"``。"""
    return _active_theme


def set_active_theme(name: str) -> bool:
    """切换当前配色；``name`` 仅接受 ``light``，其余值一律按 ``dark``。

    返回是否发生了切换（目标主题与当前一致时返回 ``False``，不做任何改动）。
    """
    global _active_theme
    normalized = "light" if str(name).strip().lower() == "light" else "dark"
    if normalized == _active_theme:
        return False
    _active_theme = normalized
    _apply_palette(LIGHT_COLORS if normalized == "light" else DARK_COLORS)
    return True


_QSS = Template(
    """
* { outline: none; }

QMainWindow, QWidget#RootWindow { background-color: $window; }
QWidget { color: $text; font-family: "$font"; font-size: 10pt; }

/* ---------- 顶部栏 ---------- */
QFrame#TopBar {
    background-color: $panel;
    border: 1px solid $border_soft;
    border-radius: 12px;
}
QLabel#AppTitle { font-size: 15pt; font-weight: 700; color: $text; }
QLabel#VersionChip {
    background-color: $elevated; color: $text_muted;
    border-radius: 9px; padding: 2px 10px; font-size: 9pt;
}
QLabel#UpdateHint { font-size: 9pt; }
QLabel#AppLogo { background: transparent; }

/* ---------- 卡片 ---------- */
QFrame#Card {
    background-color: $panel;
    border: 1px solid $border_soft;
    border-radius: 12px;
}
QLabel#CardTitle { font-size: 11pt; font-weight: 600; color: $text; }

/* ---------- 状态徽标（Badge） ---------- */
QLabel#Badge { border-radius: 10px; padding: 3px 12px; font-size: 9pt; font-weight: 600; }
QLabel#Badge[state="idle"]    { background-color: $elevated;     color: $text_muted; }
QLabel#Badge[state="accent"]  { background-color: $accent_soft;  color: $accent; }
QLabel#Badge[state="info"]    { background-color: $info_soft;    color: $info; }
QLabel#Badge[state="success"] { background-color: $success_soft; color: $success; }
QLabel#Badge[state="warning"] { background-color: $warning_soft; color: $warning; }
QLabel#Badge[state="danger"]  { background-color: $danger_soft;  color: $danger; }

/* ---------- 按钮 ---------- */
QPushButton {
    background-color: $elevated;
    color: $text;
    border: 1px solid $border;
    border-radius: 8px;
    padding: 7px 16px;
}
QPushButton:hover { background-color: $panel_alt; border-color: $border_strong; }
QPushButton:pressed { background-color: $pressed; }
QPushButton:disabled { color: $text_faint; background-color: $panel; border-color: $border_soft; }

QPushButton#GhostButton { background-color: transparent; border: 1px solid $border; }
QPushButton#GhostButton:hover { background-color: $panel_alt; }

QPushButton#ThemeButton { background-color: transparent; border: 1px solid $border; color: $text_muted; padding: 4px 12px; }
QPushButton#ThemeButton:hover { color: $accent; border-color: $accent; background-color: $panel_alt; }

QPushButton#DebugButton { background-color: transparent; border: 1px solid $border; color: $text_muted; padding: 4px 12px; }
QPushButton#DebugButton:hover { color: $warning; border-color: $warning; background-color: $panel_alt; }
QPushButton#DebugButton:checked { color: $warning; border-color: $warning; background-color: $warning_soft; }

QPushButton#StopButton { background-color: transparent; border: 1px solid $border; color: $text_muted; }
QPushButton#StopButton:hover { color: $danger; border-color: $danger; }
QPushButton#StopButton:disabled { color: $text_faint; border-color: $border_soft; }

QPushButton#PrimaryButton {
    background-color: $accent; color: #FFFFFF; border: none;
    border-radius: 10px; padding: 12px 36px;
    font-size: 12pt; font-weight: 700;
}
QPushButton#PrimaryButton:hover { background-color: $accent_hover; }
QPushButton#PrimaryButton:pressed { background-color: $accent_pressed; }
QPushButton#PrimaryButton:disabled { background-color: $accent_disabled; color: $accent_disabled_text; }

/* ---------- 输入控件 ---------- */
QLineEdit {
    background-color: $window; color: $text;
    border: 1px solid $border; border-radius: 8px;
    padding: 7px 10px; selection-background-color: $accent;
}
QLineEdit:hover { border-color: $border_strong; }
QLineEdit:focus { border-color: $accent; }

QSpinBox, QDoubleSpinBox {
    background-color: $window; color: $text;
    border: 1px solid $border; border-radius: 8px; padding: 6px 8px;
}
QSpinBox:focus, QDoubleSpinBox:focus { border-color: $accent; }

QCheckBox { spacing: 8px; color: $text; }
QCheckBox::indicator {
    width: 16px; height: 16px; border-radius: 5px;
    border: 1px solid $border; background-color: $window;
}
QCheckBox::indicator:hover { border-color: $accent; }
QCheckBox::indicator:checked { background-color: $accent; border-color: $accent; }
QCheckBox:disabled { color: $text_faint; }

QRadioButton { spacing: 8px; color: $text; }
QRadioButton::indicator {
    width: 16px; height: 16px; border-radius: 8px;
    border: 1px solid $border; background-color: $window;
}
QRadioButton::indicator:hover { border-color: $accent; }
QRadioButton::indicator:checked {
    background-color: $accent; border: 1px solid $accent; border-radius: 8px;
}
QRadioButton:disabled { color: $text_faint; }

QLineEdit:disabled {
    color: $text_faint; background-color: $panel; border-color: $border_soft;
}

QLabel#FieldCaption { color: $text_muted; font-weight: 600; }
QLabel#DropHint[mode="inplace"] { color: $warning; }

/* ---------- 拖拽区 ---------- */
QFrame#DropZone {
    border: 2px dashed $border_strong;
    border-radius: 14px;
    background-color: $drop_soft;
}
QFrame#DropZone[active="true"] {
    border-color: $accent;
    background-color: $accent_soft;
}
QLabel#DropIcon { font-size: 22pt; color: $accent; }
QLabel#DropTitle { font-size: 12pt; font-weight: 700; color: $text; }
QLabel#DropHint { color: $text_muted; font-size: 9pt; }

/* ---------- 表格 ---------- */
QTableWidget {
    background-color: $panel;
    alternate-background-color: $table_alt;
    border: none;
    gridline-color: $border_soft;
    border-radius: 8px;
}
QTableWidget::item { padding: 6px 8px; border: none; }
QTableWidget::item:selected { background-color: $accent_soft; color: $text; }
QHeaderView::section {
    background-color: $panel; color: $text_muted;
    border: none; border-bottom: 1px solid $border_soft;
    padding: 8px; font-weight: 600;
}
QTableCornerButton::section { background-color: $panel; border: none; }

/* ---------- 进度条 ---------- */
QProgressBar {
    background-color: $track; border: none; border-radius: 6px;
    height: 12px; text-align: center; color: $text_muted; font-size: 9pt;
}
QProgressBar::chunk { background-color: $accent; border-radius: 6px; }

/* ---------- 日志 ---------- */
QTextEdit#LogView {
    background-color: $window;
    border: 1px solid $border_soft;
    border-radius: 8px;
    padding: 6px;
}

/* ---------- 统计卡片 ---------- */
QFrame#StatCard {
    background-color: $panel_alt;
    border: 1px solid $border_soft;
    border-radius: 10px;
}
QLabel#StatValue { font-size: 17pt; font-weight: 700; }
QLabel#StatCaption { color: $text_muted; font-size: 9pt; }

/* ---------- 滚动条 ---------- */
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: $scroll_handle; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: $scroll_handle_hover; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: $scroll_handle; border-radius: 5px; min-width: 30px; }
QScrollBar::handle:horizontal:hover { background: $scroll_handle_hover; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* ---------- 底部栏 ---------- */
QFrame#FooterBar { background: transparent; }
QLabel#FooterStatus { color: $text_muted; }

/* ---------- 其他 ---------- */
QSplitter::handle { background-color: transparent; }
QToolTip {
    background-color: $elevated; color: $text;
    border: 1px solid $border; padding: 6px 8px; border-radius: 6px;
}
QDialog { background-color: $window; }
QMessageBox { background-color: $panel; }
"""
)


def build_stylesheet() -> str:
    """按当前生效调色板生成全局样式表。"""
    values = dict(COLORS)
    values["font"] = FONT_FAMILY
    return _QSS.substitute(values)


_apply_palette(DARK_COLORS)
