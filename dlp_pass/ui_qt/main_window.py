"""主窗口：整体布局、状态管理与 QThread 信号连接。

界面结构：
- 顶部栏：应用标题 / 版本号 / 数据库连接徽标（含加载动画）/ 映射规则版本徽标；
- 左侧：源文件卡片（拖拽区 + 工具栏 + 文件表格）、输出方式与目录卡片
  （「输出到指定目录 / 原地解密覆盖」单选切换，原地模式下目录选择区域禁用）；
- 右侧：白名单进程队列表格、任务统计卡片、实时日志面板；
- 底部：状态文本、进度条、停止与「开始恢复 / 导出」主按钮。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .qt_compat import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QColor,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    Qt,
    QTimer,
    QVBoxLayout,
    QWidget,
)

import dlp_pass
from ..config import AppConfig
from ..core.decrypt_service import DecryptService
from ..core.mapping_loader import MappingSet
from ..core.output_mode import OutputMode, plan_decrypt_tasks, validate_output_dir
from ..core.source_reader import SourceReader
from ..core.verifier import EncryptionVerifier
from ..core.writer_factory import DisguisedWriterFactory
from ..version_check import RELEASE_PAGE_URL, is_newer
from . import settings, theme
from .assets import app_icon, logo_pixmap
from .widgets import Card, DropZone, LogPanel, StatCard, StatusBadge, format_size
from .workers import DecryptWorker, ScanWorker, VersionCheckTask

_logger = logging.getLogger(__name__)

_STATUS_TEXT = {
    "pending": "待处理",
    "running": "解密中",
    "success": "已解密",
    "failed": "失败",
    "skipped": "已跳过",
    "cancelled": "已取消",
}
_STATUS_STATE = {
    "pending": "idle",
    "running": "info",
    "success": "success",
    "failed": "danger",
    "skipped": "warning",
    "cancelled": "idle",
}

# 行数阈值：前 N 行使用徽标控件（美观），超出后使用纯文本状态（大批量性能）
_BADGE_ROW_LIMIT = 600
# 统计/按钮刷新的合并延迟（毫秒）：合并高频状态变化，避免 O(n^2) 刷新
_UI_REFRESH_DELAY_MS = 150


@dataclass
class FileEntry:
    """界面侧的文件条目状态。"""

    item_id: str
    source: Path
    relative_output: Path
    suffix: str
    size: int | None
    encrypted: bool | None = None
    status: str = "pending"
    note: str = ""


class MainWindow(QMainWindow):
    """应用主窗口。"""

    def __init__(self, config: AppConfig, mapping: MappingSet):
        super().__init__()
        self._config = config
        self._mapping = mapping

        # ---- 业务组件（复用 dlp_pass.core，解密逻辑与界面完全解耦）----
        self._verifier = EncryptionVerifier(config)
        self._service = DecryptService(
            mapping=self._mapping,
            verifier=self._verifier,
            writer_factory=DisguisedWriterFactory(config),
            source_reader=SourceReader(config.encryption_marker),
        )

        # ---- 状态 ----
        self._entries: dict[str, FileEntry] = {}
        self._row_index: dict[str, int] = {}
        self._status_cells: dict[str, tuple[str, object]] = {}  # item_id -> ("badge"|"item", 控件)
        self._note_items: dict[str, QTableWidgetItem] = {}
        self._scan_worker: ScanWorker | None = None
        self._decrypt_worker: DecryptWorker | None = None
        self._version_task: VersionCheckTask | None = None
        self._pending_scan_paths: list[list[str]] = []
        self._running = False
        # 版本检查结果状态（"update" / "latest" / None=未检查或检查失败）
        # 用于主题切换时重新渲染版本号的配色与悬浮提示。
        self._version_status: str | None = None
        self._version_latest: str = ""

        # 统计/开始按钮的合并刷新（避免逐个文件 O(n) 刷新导致大批量卡顿）
        self._ui_refresh_timer = QTimer(self)
        self._ui_refresh_timer.setSingleShot(True)
        self._ui_refresh_timer.setInterval(_UI_REFRESH_DELAY_MS)
        self._ui_refresh_timer.timeout.connect(self._flush_ui_refresh)

        self.setWindowTitle("DLP 解密助手")
        icon = app_icon()
        if not icon.isNull():
            self.setWindowIcon(icon)
        self.setMinimumSize(1060, 660)
        self.resize(1180, 760)
        self.setAcceptDrops(True)

        self._build_ui()
        if self._mapping.is_fallback:
            self._mapping_badge.set_state("warning", "映射规则 内置兜底")
            self._log_panel.add_entry(
                "未找到可用的映射规则文件，已启用内置兜底规则（详见 mappings/README.md）。", "warning"
            )
        else:
            self._mapping_badge.set_state(
                "accent", f"映射规则 {self._mapping.version} · {len(self._mapping.rules)} 条"
            )
        self._log_panel.add_entry(
            "欢迎使用 DLP 解密助手：拖入文件或文件夹后点击「开始恢复 / 导出」。", "info"
        )
        if self._config.check_updates:
            self._start_version_check()

    # ================================================================== 布局

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("RootWindow")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(18, 14, 18, 12)
        outer.setSpacing(12)

        outer.addWidget(self._build_top_bar())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(12)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        outer.addWidget(splitter, 1)

        outer.addWidget(self._build_footer())

    def _build_top_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("TopBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(10)

        logo_pixmap_value = logo_pixmap(28)
        if not logo_pixmap_value.isNull():
            logo = QLabel()
            logo.setObjectName("AppLogo")
            logo.setPixmap(logo_pixmap_value)
            logo.setFixedSize(28, 28)
            layout.addWidget(logo)

        title = QLabel("DLP 解密助手")
        title.setObjectName("AppTitle")
        self._version_label = QLabel(f"v{dlp_pass.__version__}")
        self._version_label.setObjectName("VersionChip")
        self._update_hint = QLabel()
        self._update_hint.setObjectName("UpdateHint")
        self._update_hint.setTextFormat(Qt.TextFormat.RichText)
        self._update_hint.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self._update_hint.setOpenExternalLinks(True)
        self._update_hint.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_hint.setVisible(False)

        self._mapping_badge = StatusBadge("映射规则 —", "idle")

        # 主题切换按钮：位于「映射规则版本号」徽标左侧
        self._theme_button = QPushButton()
        self._theme_button.setObjectName("ThemeButton")
        self._theme_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._theme_button.clicked.connect(self._on_theme_toggled)
        self._update_theme_button()

        layout.addWidget(title)
        layout.addWidget(self._version_label)
        layout.addWidget(self._update_hint)
        layout.addStretch(1)
        layout.addWidget(self._theme_button)
        layout.addWidget(self._mapping_badge)
        return bar

    # --------------------------------------------------------- 版本检查

    def _start_version_check(self) -> None:
        """启动后台版本检查；网络异常时保持默认外观并静默降级。"""
        task = VersionCheckTask()
        task.signals.checked.connect(self._on_version_checked)
        task.signals.failed.connect(self._on_version_check_failed)
        self._version_task = task
        task.start()

    def _on_version_checked(self, latest: str) -> None:
        if is_newer(latest, dlp_pass.__version__):
            self._version_status = "update"
            self._version_latest = latest.lstrip("vV")
            color = theme.COLORS["danger"]
            self._version_label.setStyleSheet(f"color: {color};")
            self._version_label.setToolTip(f"有新版本可用 v{self._version_latest}")
            self._update_hint.setText(
                f'<a href="{RELEASE_PAGE_URL}" '
                f'style="color: {color}; text-decoration: none;">有新版本可用</a>'
            )
            self._update_hint.setToolTip(
                f"最新版本：{self._version_latest}，点击打开 Release 页面"
            )
            self._update_hint.setVisible(True)
            _logger.info("检测到新版本：%s（当前 %s）", latest, dlp_pass.__version__)
        else:
            self._version_status = "latest"
            self._version_latest = ""
            self._version_label.setStyleSheet(f"color: {theme.COLORS['success']};")
            self._version_label.setToolTip("当前为最新版本")
            self._update_hint.clear()
            self._update_hint.setToolTip("")
            self._update_hint.setVisible(False)
            _logger.info("当前版本已是最新：%s", dlp_pass.__version__)

    def _on_version_check_failed(self) -> None:
        """版本源不可用：仅显示当前版本，不标色、不提示、无悬浮信息。"""
        self._version_status = None
        self._version_latest = ""
        self._version_label.setStyleSheet("")
        self._version_label.setToolTip("")
        self._update_hint.clear()
        self._update_hint.setToolTip("")
        self._update_hint.setVisible(False)
        _logger.debug("版本检查失败，已静默降级")

    # --------------------------------------------------------- 主题切换

    def _on_theme_toggled(self) -> None:
        """一键切换暗色 / 亮色配色并持久化用户偏好。"""
        target = "light" if theme.active_theme() == "dark" else "dark"
        theme.set_active_theme(target)
        settings.save_theme(target)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme.build_stylesheet())
        self._refresh_theme_dependent_colors()
        self._log_panel.add_entry(
            "已切换到%s模式。" % ("暗色" if target == "dark" else "亮色"), "info"
        )

    def _update_theme_button(self) -> None:
        """按当前主题刷新按钮文本与悬浮提示（文本显示当前模式）。"""
        dark = theme.active_theme() == "dark"
        self._theme_button.setText("暗色模式" if dark else "亮色模式")
        self._theme_button.setToolTip("点击切换为亮色模式" if dark else "点击切换为暗色模式")

    def _refresh_theme_dependent_colors(self) -> None:
        """主题切换后刷新不走全局 QSS 的内联颜色（统计卡、版本号、彩色状态文本）。"""
        self._update_theme_button()
        self._stat_total.set_color(theme.COLORS["accent"])
        self._stat_success.set_color(theme.COLORS["success"])
        self._stat_failed.set_color(theme.COLORS["danger"])
        self._stat_skipped.set_color(theme.COLORS["warning"])
        if self._version_status == "update":
            color = theme.COLORS["danger"]
            self._version_label.setStyleSheet(f"color: {color};")
            self._update_hint.setText(
                f'<a href="{RELEASE_PAGE_URL}" '
                f'style="color: {color}; text-decoration: none;">有新版本可用</a>'
            )
        elif self._version_status == "latest":
            self._version_label.setStyleSheet(f"color: {theme.COLORS['success']};")
        else:
            self._version_label.setStyleSheet("")
        for item_id, (mode, widget) in self._status_cells.items():
            if mode != "item":  # 徽标控件由 QSS 动态属性驱动，重新 polish 即可
                continue
            entry = self._entries.get(item_id)
            if entry is None:
                continue
            state = _STATUS_STATE.get(entry.status, "idle")
            widget.setForeground(
                QColor(theme.STATE_COLORS.get(state, theme.COLORS["text_muted"]))
            )

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # 源文件卡片
        source_card = Card("源文件")
        self._drop_zone = DropZone()
        self._drop_zone.setCursor(Qt.CursorShape.PointingHandCursor)
        self._drop_zone.paths_dropped.connect(self._on_paths_dropped)
        self._drop_zone.clicked.connect(self._on_add_files)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        add_files_button = QPushButton("添加文件")
        add_files_button.clicked.connect(self._on_add_files)
        add_folder_button = QPushButton("添加文件夹")
        add_folder_button.clicked.connect(self._on_add_folder)
        clear_button = QPushButton("清空列表")
        clear_button.clicked.connect(self._on_clear)
        self._skip_check = QCheckBox("自动跳过未加密文件")
        self._skip_check.setChecked(True)
        self._skip_check.toggled.connect(self._reapply_skip_flags)
        toolbar.addWidget(add_files_button)
        toolbar.addWidget(add_folder_button)
        toolbar.addWidget(clear_button)
        toolbar.addStretch(1)
        toolbar.addWidget(self._skip_check)

        self._file_table = QTableWidget(0, 4)
        self._file_table.setHorizontalHeaderLabels(["文件名", "大小", "状态", "说明"])
        self._file_table.verticalHeader().setVisible(False)
        self._file_table.verticalHeader().setDefaultSectionSize(40)
        self._file_table.setShowGrid(False)
        self._file_table.setAlternatingRowColors(True)
        self._file_table.setWordWrap(False)
        self._file_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._file_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self._file_table.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self._file_table.setColumnWidth(1, 88)
        self._file_table.setColumnWidth(2, 96)
        self._file_table.setColumnWidth(3, 250)

        source_card.body().addWidget(self._drop_zone)
        source_card.body().addLayout(toolbar)
        source_card.body().addWidget(self._file_table, 1)

        # 输出方式与目录卡片（模式切换 + 目录选择）
        output_card = Card("输出方式与目录")
        mode_row = QHBoxLayout()
        mode_row.setSpacing(14)
        mode_caption = QLabel("输出方式")
        mode_caption.setObjectName("FieldCaption")
        self._mode_group = QButtonGroup(self)
        self._mode_directory_radio = QRadioButton(OutputMode.DIRECTORY.label)
        self._mode_inplace_radio = QRadioButton(OutputMode.IN_PLACE.label)
        self._mode_group.addButton(self._mode_directory_radio)
        self._mode_group.addButton(self._mode_inplace_radio)
        self._mode_directory_radio.setChecked(True)
        self._mode_directory_radio.toggled.connect(self._on_output_mode_changed)
        self._mode_inplace_radio.toggled.connect(self._on_output_mode_changed)
        mode_row.addWidget(mode_caption)
        mode_row.addWidget(self._mode_directory_radio)
        mode_row.addWidget(self._mode_inplace_radio)
        mode_row.addStretch(1)

        dir_row = QHBoxLayout()
        dir_row.setSpacing(8)
        self._output_edit = QLineEdit(str(self._config.default_output_dir))
        browse_button = QPushButton("浏览…")
        browse_button.clicked.connect(self._on_browse_output)
        dir_row.addWidget(self._output_edit, 1)
        dir_row.addWidget(browse_button)
        self._output_dir_widgets = (self._output_edit, browse_button)

        self._output_hint = QLabel()
        self._output_hint.setObjectName("DropHint")
        self._output_hint.setWordWrap(True)

        output_card.body().addLayout(mode_row)
        output_card.body().addLayout(dir_row)
        output_card.body().addWidget(self._output_hint)
        self._on_output_mode_changed(True)  # 初始状态：目录模式可用

        layout.addWidget(source_card, 1)
        layout.addWidget(output_card, 0)
        return panel

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # 白名单进程队列
        queue_card = Card("白名单进程队列")
        queue_hint = QLabel("根据拖入文件的后缀，从映射库实时匹配伪装进程（主 / 备用）")
        queue_hint.setObjectName("DropHint")
        self._queue_table = QTableWidget(0, 4)
        self._queue_table.setHorizontalHeaderLabels(["文件后缀", "文件数", "主进程", "备用进程"])
        self._queue_table.verticalHeader().setVisible(False)
        self._queue_table.verticalHeader().setDefaultSectionSize(34)
        self._queue_table.setShowGrid(False)
        self._queue_table.setAlternatingRowColors(True)
        self._queue_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._queue_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        queue_header = self._queue_table.horizontalHeader()
        queue_header.setHighlightSections(False)
        queue_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        queue_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        queue_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        queue_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._queue_table.setColumnWidth(0, 90)
        self._queue_table.setColumnWidth(1, 70)
        self._queue_table.setColumnWidth(2, 130)
        queue_card.body().addWidget(queue_hint)
        queue_card.body().addWidget(self._queue_table, 1)

        # 任务统计
        stats_card = Card("任务统计")
        stats_row = QHBoxLayout()
        stats_row.setSpacing(8)
        self._stat_total = StatCard("总任务", theme.COLORS["accent"])
        self._stat_success = StatCard("成功", theme.COLORS["success"])
        self._stat_failed = StatCard("失败", theme.COLORS["danger"])
        self._stat_skipped = StatCard("跳过", theme.COLORS["warning"])
        for card in (self._stat_total, self._stat_success, self._stat_failed, self._stat_skipped):
            stats_row.addWidget(card, 1)
        stats_card.body().addLayout(stats_row)

        # 实时日志
        log_card = Card("运行日志")
        log_toolbar = QHBoxLayout()
        log_toolbar.addStretch(1)
        clear_log_button = QPushButton("清空日志")
        clear_log_button.setObjectName("GhostButton")
        clear_log_button.clicked.connect(lambda: self._log_panel.clear_log())
        log_toolbar.addWidget(clear_log_button)
        self._log_panel = LogPanel()
        log_card.body().addLayout(log_toolbar)
        log_card.body().addWidget(self._log_panel, 1)

        layout.addWidget(queue_card, 2)
        layout.addWidget(stats_card, 0)
        layout.addWidget(log_card, 3)
        return panel

    def _build_footer(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("FooterBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(14)

        self._status_label = QLabel("就绪")
        self._status_label.setObjectName("FooterStatus")

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFormat("")
        self._progress.setMinimumWidth(260)

        self._stop_button = QPushButton("停止")
        self._stop_button.setObjectName("StopButton")
        self._stop_button.setEnabled(False)
        self._stop_button.clicked.connect(self._on_stop)

        self._start_button = QPushButton("开始恢复 / 导出")
        self._start_button.setObjectName("PrimaryButton")
        self._start_button.setEnabled(False)
        self._start_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._start_button.clicked.connect(self._on_start)

        layout.addWidget(self._status_label)
        layout.addWidget(self._progress, 1)
        layout.addWidget(self._stop_button)
        layout.addWidget(self._start_button)
        return bar

    # ============================================================ 拖拽与添加

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self._on_paths_dropped(paths)
        event.acceptProposedAction()

    def _on_paths_dropped(self, paths: list[str]) -> None:
        if not paths:
            return
        self._log_panel.add_entry(f"已添加 {len(paths)} 个路径，开始扫描…", "info")
        if self._scan_worker is not None and self._scan_worker.isRunning():
            self._pending_scan_paths.append(list(paths))
            self._log_panel.add_entry("已有扫描任务进行中，新路径将排队处理。", "info")
            return
        self._start_scan(list(paths))

    def _on_add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "选择要解密的文件")
        if paths:
            self._on_paths_dropped(paths)

    def _on_add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择要解密的文件夹")
        if folder:
            self._on_paths_dropped([folder])

    def _on_browse_output(self) -> None:
        if self._current_output_mode() is not OutputMode.DIRECTORY:
            return
        folder = QFileDialog.getExistingDirectory(
            self, "选择解密输出目录", self._output_edit.text().strip()
        )
        if folder:
            self._output_edit.setText(folder)

    # ============================================================== 输出模式

    def _current_output_mode(self) -> OutputMode:
        """当前选择的解密输出方式。"""
        if self._mode_inplace_radio.isChecked():
            return OutputMode.IN_PLACE
        return OutputMode.DIRECTORY

    def _on_output_mode_changed(self, checked: bool = True) -> None:
        """模式切换即时响应界面状态（无需刷新）：

        - 「输出到指定目录」：输出目录输入框与「浏览…」按钮可用；
        - 「原地解密覆盖」：输出目录选择区域整体禁用（置灰），用户无法修改路径。
        """
        if not checked:
            return
        directory_mode = self._mode_directory_radio.isChecked()
        for widget in self._output_dir_widgets:
            widget.setEnabled(directory_mode)
        self._output_hint.setText(
            "解密结果写入所选目录并保留原目录结构，原始加密文件保持不变。"
            if directory_mode
            else "直接覆盖原始文件路径，输出目录选择区域已禁用；原始文件将不再保留密文版本。"
        )
        self._output_hint.setProperty("mode", "directory" if directory_mode else "inplace")
        self._output_hint.style().unpolish(self._output_hint)
        self._output_hint.style().polish(self._output_hint)
        status_label = getattr(self, "_status_label", None)  # 首次构建时底部栏尚未创建
        if status_label is not None:
            status_label.setText("就绪" if directory_mode else "就绪（原地解密覆盖）")

    def _on_clear(self) -> None:
        if self._running:
            QMessageBox.information(self, "提示", "正在解密中，请先停止任务。")
            return
        if not self._entries:
            return
        self._entries.clear()
        self._row_index.clear()
        self._status_cells.clear()
        self._note_items.clear()
        self._ui_refresh_timer.stop()
        self._file_table.setRowCount(0)
        self._queue_table.setRowCount(0)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFormat("")
        self._status_label.setText("就绪")
        self._refresh_stats()
        self._refresh_start_button()
        self._log_panel.add_entry("已清空文件列表。", "info")

    # ============================================================== 扫描流程

    def _start_scan(self, paths: list[str]) -> None:
        worker = ScanWorker(self._verifier, paths)
        worker.entry_ready.connect(self._on_scan_entry_ready)
        worker.progress.connect(self._on_scan_progress)
        worker.finished_scan.connect(self._on_scan_finished)
        worker.failed.connect(self._on_scan_failed)
        self._scan_worker = worker
        self._progress.setRange(0, 0)  # 不确定进度（扫描中）
        self._status_label.setText("正在扫描文件…")
        worker.start()

    def _on_scan_entry_ready(self, payload: dict) -> None:
        """单个文件「收集 + 加密检测」完成（合并事件）。"""
        self._add_file_row(payload)

    def _on_scan_progress(self, count: int) -> None:
        if not self._running:
            self._status_label.setText(f"正在扫描… 已检测 {count} 个文件")

    def _on_scan_finished(self, added: int, truncated: bool) -> None:
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFormat("")
        self._log_panel.add_entry(
            f"扫描完成：本次新增 {added} 个文件（列表共 {len(self._entries)} 个）。", "info"
        )
        if truncated:
            self._log_panel.add_entry(
                f"已达到单次扫描上限（{ScanWorker.MAX_FILES} 个文件），其余文件未加入列表。", "warning"
            )
        if not self._running:
            self._status_label.setText("扫描完成")
        self._refresh_queue_table()
        self._refresh_stats()
        self._refresh_start_button()
        if self._pending_scan_paths:
            next_paths = self._pending_scan_paths.pop(0)
            self._start_scan(next_paths)

    def _on_scan_failed(self, message: str) -> None:
        self._log_panel.add_entry(f"扫描出错：{message}", "error")

    # ============================================================== 文件表格

    def _add_file_row(self, payload: dict) -> bool:
        item_id = payload["item_id"]
        if item_id in self._entries:
            return False
        encrypted = payload.get("encrypted")
        status, note = self._initial_status(encrypted)
        entry = FileEntry(
            item_id=item_id,
            source=payload["source"],
            relative_output=payload["relative_output"],
            suffix=payload["suffix"],
            size=payload["size"],
            encrypted=encrypted,
            status=status,
            note=note,
        )
        self._entries[item_id] = entry

        row = self._file_table.rowCount()
        self._file_table.insertRow(row)

        name_item = QTableWidgetItem(payload["name"])
        name_item.setToolTip(str(payload["source"]))
        size_item = QTableWidgetItem(format_size(payload["size"]))
        size_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        note_item = QTableWidgetItem(note)
        note_item.setToolTip(note)

        self._file_table.setItem(row, 0, name_item)
        self._file_table.setItem(row, 1, size_item)
        self._file_table.setItem(row, 3, note_item)

        self._row_index[item_id] = row
        self._status_cells[item_id] = self._create_status_cell(
            row, _STATUS_TEXT[status], _STATUS_STATE[status]
        )
        self._note_items[item_id] = note_item

        self._mark_ui_dirty()
        return True

    def _initial_status(self, encrypted: object) -> tuple[str, str]:
        """根据加密检测结果与「跳过未加密」勾选状态，推导条目的初始状态与说明。"""
        if encrypted is False:
            if self._skip_check.isChecked():
                return "skipped", "未检测到加密标识，已跳过"
            return "pending", "未加密（未勾选跳过，将强制导出）"
        if encrypted is None:
            return "pending", "无法判定加密状态（将按密文处理）"
        return "pending", ""

    def _create_status_cell(self, row: int, text: str, state: str) -> tuple[str, object]:
        """创建状态单元格：前 _BADGE_ROW_LIMIT 行使用徽标控件，其余使用彩色文本。

        大批量列表（数千行）使用纯文本可避免大量控件创建导致的界面卡顿。
        """
        if row < _BADGE_ROW_LIMIT:
            badge = StatusBadge(text, state)
            container = QWidget()
            container_layout = QHBoxLayout(container)
            container_layout.setContentsMargins(6, 0, 6, 0)
            container_layout.addWidget(badge)
            self._file_table.setCellWidget(row, 2, container)
            return ("badge", badge)
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setForeground(QColor(theme.STATE_COLORS.get(state, theme.COLORS["text_muted"])))
        self._file_table.setItem(row, 2, item)
        return ("item", item)

    def _apply_status_cell(self, item_id: str, text: str, state: str) -> None:
        cell = self._status_cells.get(item_id)
        if cell is None:
            return
        mode, widget = cell
        if mode == "badge":
            widget.set_state(state, text)
        else:
            widget.setText(text)
            widget.setForeground(QColor(theme.STATE_COLORS.get(state, theme.COLORS["text_muted"])))

    def _set_entry_status(self, item_id: str, status: str, note: str = "") -> None:
        entry = self._entries.get(item_id)
        if entry is None:
            return
        entry.status = status
        entry.note = note
        self._apply_status_cell(
            item_id, _STATUS_TEXT.get(status, status), _STATUS_STATE.get(status, "idle")
        )
        note_item = self._note_items.get(item_id)
        if note_item is not None:
            note_item.setText(note)
            note_item.setToolTip(note)
        self._mark_ui_dirty()

    def _mark_ui_dirty(self) -> None:
        """标记统计/按钮需要刷新（合并到定时器中，避免逐条 O(n) 刷新）。"""
        if not self._ui_refresh_timer.isActive():
            self._ui_refresh_timer.start()

    def _flush_ui_refresh(self) -> None:
        self._refresh_stats()
        self._refresh_start_button()

    def _reapply_skip_flags(self) -> None:
        """「自动跳过未加密文件」勾选变化时刷新相关条目状态。"""
        skip = self._skip_check.isChecked()
        for entry in self._entries.values():
            if entry.encrypted is not False:
                continue
            if skip and entry.status == "pending":
                self._set_entry_status(entry.item_id, "skipped", "未检测到加密标识，已跳过")
            elif not skip and entry.status == "skipped":
                self._set_entry_status(entry.item_id, "pending", "未加密（未勾选跳过，将强制导出）")

    def _refresh_stats(self) -> None:
        counts = {"pending": 0, "running": 0, "success": 0, "failed": 0, "skipped": 0, "cancelled": 0}
        for entry in self._entries.values():
            counts[entry.status] = counts.get(entry.status, 0) + 1
        self._stat_total.set_value(len(self._entries))
        self._stat_success.set_value(counts["success"])
        self._stat_failed.set_value(counts["failed"])
        self._stat_skipped.set_value(counts["skipped"])

    def _refresh_start_button(self) -> None:
        has_pending = any(entry.status == "pending" for entry in self._entries.values())
        self._start_button.setEnabled(has_pending and not self._running)

    # ============================================================ 进程队列表

    def _refresh_queue_table(self) -> None:
        """按当前映射版本刷新「后缀 -> 进程队列」表（本地内存查询，无需后台线程）。"""
        suffix_counts: dict[str, int] = {}
        for entry in self._entries.values():
            suffix_counts[entry.suffix] = suffix_counts.get(entry.suffix, 0) + 1
        if not suffix_counts:
            self._queue_table.setRowCount(0)
            return
        rows = [
            (suffix, count, self._mapping.get_process_queue(suffix))
            for suffix, count in sorted(suffix_counts.items(), key=lambda item: (-item[1], item[0]))
        ]
        self._populate_queue_table(rows)

    def _populate_queue_table(self, rows: list) -> None:
        self._queue_table.setRowCount(len(rows))
        for row, (suffix, count, queue) in enumerate(rows):
            primary = queue[0] if queue else "-"
            alternatives = "、".join(queue[1:]) if len(queue) > 1 else "-"
            values = (suffix, str(count), primary, alternatives)
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                if column == 1:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._queue_table.setItem(row, column, item)

    # ============================================================== 解密流程

    def _on_start(self) -> None:
        if self._running:
            return
        pending = [entry for entry in self._entries.values() if entry.status == "pending"]
        if not pending:
            QMessageBox.information(self, "提示", "没有待处理的任务（列表为空或均已跳过 / 完成）。")
            return

        mode = self._current_output_mode()
        output_root: Path | None = None
        if mode is OutputMode.DIRECTORY:
            valid, message, resolved = validate_output_dir(self._output_edit.text())
            if not valid:
                QMessageBox.warning(self, "输出目录无效", message)
                self._log_panel.add_entry(f"已阻止开始批处理：{message}", "warning")
                self._output_edit.setFocus()
                return
            output_root = resolved
        else:
            answer = QMessageBox.question(
                self,
                "确认原地解密覆盖",
                f"即将对 {len(pending)} 个文件执行「原地解密覆盖」：解密结果会替换各自的原文件。\n"
                "程序会先确认导出内容为明文再覆盖，确认失败时原文件保持不变。\n确定继续吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self._log_panel.add_entry("用户取消了原地解密覆盖。", "warning")
                return

        tasks, conflicts = plan_decrypt_tasks(pending, mode, output_root)
        for item_id, note in conflicts:
            self._set_entry_status(item_id, "skipped", note)
        if not tasks:
            QMessageBox.information(self, "提示", "没有可执行的任务。")
            return

        location = f"输出目录：{output_root}" if output_root is not None else "目标：各文件原始路径"
        self._running = True
        self._start_button.setEnabled(False)
        self._stop_button.setEnabled(True)
        self._progress.setRange(0, len(tasks))
        self._progress.setValue(0)
        self._progress.setFormat("%v / %m")
        self._status_label.setText(f"解密中… 0/{len(tasks)}")
        self._log_panel.add_entry(f"开始批量解密：共 {len(tasks)} 个文件，模式：{mode.label}，{location}", "info")

        if self._decrypt_worker is not None and not self._decrypt_worker.isRunning():
            self._decrypt_worker.deleteLater()
            self._decrypt_worker = None
        worker = DecryptWorker(self._service, tasks)
        worker.task_started.connect(self._on_task_started)
        worker.task_finished.connect(self._on_task_finished)
        worker.progress.connect(self._on_decrypt_progress)
        worker.log.connect(self._on_decrypt_log)
        worker.finished_all.connect(self._on_decrypt_finished)
        self._decrypt_worker = worker
        worker.start()

    def _on_task_started(self, item_id: str) -> None:
        self._set_entry_status(item_id, "running", "")

    def _on_task_finished(self, item_id: str, status: str, note: str) -> None:
        self._set_entry_status(item_id, status, note)
        entry = self._entries.get(item_id)
        name = entry.source.name if entry else item_id
        if status == "success":
            self._log_panel.add_entry(f"已解密：{name} （{note}）", "success")
        elif status == "failed":
            self._log_panel.add_entry(f"解密失败：{name} — {note}", "error")
        elif status == "cancelled":
            self._log_panel.add_entry(f"已取消：{name}", "warning")

    def _on_decrypt_progress(self, done: int, total: int, success: int, failed: int) -> None:
        self._progress.setValue(done)
        self._status_label.setText(f"解密中… {done}/{total}")

    def _on_decrypt_log(self, message: str, level: str) -> None:
        self._log_panel.add_entry(message, level)

    def _on_decrypt_finished(self, summary: dict) -> None:
        self._running = False
        self._progress.setValue(self._progress.maximum())
        self._stop_button.setEnabled(False)
        self._status_label.setText(
            f"批量任务完成：成功 {summary['success']} · 失败 {summary['failed']}"
        )
        self._log_panel.add_entry(
            f"批量任务结束：成功 {summary['success']} · 失败 {summary['failed']}"
            f" · 取消 {summary['done'] - summary['success'] - summary['failed']}",
            "success" if summary["failed"] == 0 else "warning",
        )
        self._refresh_stats()
        self._refresh_start_button()

    def _on_stop(self) -> None:
        if self._decrypt_worker is not None and self._decrypt_worker.isRunning():
            self._decrypt_worker.cancel()
            self._status_label.setText("正在停止…")
            self._log_panel.add_entry("已请求停止，等待当前文件处理完成后结束…", "warning")

    # ================================================================== 退出

    def closeEvent(self, event):
        if self._running:
            answer = QMessageBox.question(
                self,
                "退出",
                "解密任务尚未完成，确定要退出吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            if self._decrypt_worker is not None:
                self._decrypt_worker.cancel()
                self._decrypt_worker.wait(5000)
        if self._scan_worker is not None and self._scan_worker.isRunning():
            self._scan_worker.cancel()
            self._scan_worker.wait(3000)
        self._verifier.close()
        event.accept()
