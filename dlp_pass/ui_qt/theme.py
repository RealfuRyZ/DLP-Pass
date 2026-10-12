"""PyQt6 深色主题：调色板与全局样式表（QSS）。

设计目标：深灰 / 暗蓝背景（#1E1E1E 系）+ 品牌蓝强调色，
统一圆角、微阴影、悬停反馈与现代化状态徽标（Badge）。
"""

from __future__ import annotations

from string import Template

COLORS = {
    "window": "#1E1E1E",
    "panel": "#26282D",
    "panel_alt": "#2B2E34",
    "elevated": "#31353C",
    "border": "#3A3F47",
    "border_soft": "#2F333A",
    "text": "#E8EAED",
    "text_muted": "#9BA1AB",
    "text_faint": "#6E747E",
    "accent": "#4C8DFF",
    "accent_hover": "#6DA3FF",
    "accent_pressed": "#3C78E6",
    "accent_soft": "rgba(76, 141, 255, 0.16)",
    "success": "#3FBF7F",
    "success_soft": "rgba(63, 191, 127, 0.16)",
    "danger": "#E5646C",
    "danger_soft": "rgba(229, 100, 108, 0.16)",
    "warning": "#E0A83E",
    "warning_soft": "rgba(224, 168, 62, 0.16)",
    "info": "#56A6F0",
    "info_soft": "rgba(86, 166, 240, 0.16)",
}

FONT_FAMILY = "Microsoft YaHei UI"

# 状态徽标 state 取值 -> 文本颜色（大批量列表中用纯文本状态替代徽标控件时使用）
STATE_COLORS = {
    "idle": COLORS["text_muted"],
    "accent": COLORS["accent"],
    "info": COLORS["info"],
    "success": COLORS["success"],
    "warning": COLORS["warning"],
    "danger": COLORS["danger"],
}

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
QPushButton:hover { background-color: $panel_alt; border-color: #4A505A; }
QPushButton:pressed { background-color: #24272C; }
QPushButton:disabled { color: $text_faint; background-color: $panel; border-color: $border_soft; }

QPushButton#GhostButton { background-color: transparent; border: 1px solid $border; }
QPushButton#GhostButton:hover { background-color: $panel_alt; }

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
QPushButton#PrimaryButton:disabled { background-color: #3A4A66; color: #A9B6CC; }

/* ---------- 输入控件 ---------- */
QLineEdit {
    background-color: $window; color: $text;
    border: 1px solid $border; border-radius: 8px;
    padding: 7px 10px; selection-background-color: $accent;
}
QLineEdit:hover { border-color: #4A505A; }
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

/* ---------- 拖拽区 ---------- */
QFrame#DropZone {
    border: 2px dashed #4A505A;
    border-radius: 14px;
    background-color: rgba(76, 141, 255, 0.04);
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
    alternate-background-color: #24262B;
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
    background-color: #2A2D33; border: none; border-radius: 6px;
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
QScrollBar::handle:vertical { background: #43474F; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #555C66; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: #43474F; border-radius: 5px; min-width: 30px; }
QScrollBar::handle:horizontal:hover { background: #555C66; }
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
    """生成全局样式表（深色主题）。"""
    values = dict(COLORS)
    values["font"] = FONT_FAMILY
    return _QSS.substitute(values)
