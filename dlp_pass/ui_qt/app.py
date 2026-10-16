"""应用入口：初始化 QApplication、深色主题与主窗口（PyQt6 / PyQt5 双兼容）。"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import dlp_pass
from ..config import AppConfig
from ..core.bundle import is_bundled
from ..core.mapping_loader import load_latest
from . import theme
from .main_window import MainWindow
from .qt_compat import QApplication, QFont, QMessageBox, prepare_high_dpi

_logger = logging.getLogger(__name__)


def _setup_logging() -> Path | None:
    """初始化日志：有控制台时输出到控制台；发布版（无控制台）写入本地日志文件。"""
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handlers: list[logging.Handler] = []

    if sys.stderr is not None:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        handlers.append(stream_handler)

    log_path: Path | None = None
    try:
        log_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DLP-Pass" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "dlp_pass.log"
        file_handler = RotatingFileHandler(
            log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)
    except OSError:
        pass

    logging.basicConfig(level=logging.INFO, handlers=handlers)
    return log_path


def _install_exception_hook() -> None:
    """未捕获异常时记录日志并弹出提示，避免界面无声崩溃。"""

    def hook(exc_type, exc_value, exc_traceback):
        _logger.critical("未捕获异常", exc_info=(exc_type, exc_value, exc_traceback))
        try:
            QMessageBox.critical(
                None,
                "发生未处理的异常",
                f"{exc_type.__name__}: {exc_value}\n\n详细信息已写入日志。",
            )
        except Exception:
            pass

    sys.excepthook = hook


def _load_config(project_root: Path) -> AppConfig:
    """加载配置：打包产物优先读取 exe 同目录的 config.json，其次项目根目录。"""
    candidates: list[Path] = []
    if is_bundled():
        candidates.append(Path(sys.executable).resolve().parent / "config.json")
    candidates.append(project_root / "config.json")
    for path in candidates:
        if path.is_file():
            return AppConfig.load(path)
    return AppConfig()


def _run_selftest(paths: list[str]) -> int:
    """无界面自检：加载映射规则 + 对给定文件执行完整解密流程。

    用法：``DLP-Pass.exe --selftest [待解密的加密文件...]``
    退出码：``0`` = 全部成功；``1`` = 存在解密失败。
    过程与结果写入日志文件（%LOCALAPPDATA%\\DLP-Pass\\logs\\dlp_pass.log）。
    """
    import tempfile

    from ..core.decrypt_service import DecryptService
    from ..core.models import DecryptTask
    from ..core.source_reader import SourceReader
    from ..core.verifier import EncryptionVerifier
    from ..core.writer_factory import DisguisedWriterFactory

    logger = logging.getLogger("selftest")
    project_root = Path(__file__).resolve().parents[2]
    config = _load_config(project_root)
    mapping = load_latest(config, project_root)
    logger.info("[selftest] 映射规则: %s", mapping.describe())

    verifier = EncryptionVerifier(config)
    service = DecryptService(
        mapping=mapping,
        verifier=verifier,
        writer_factory=DisguisedWriterFactory(config),
        source_reader=SourceReader(config.encryption_marker),
    )
    out_dir = Path(tempfile.gettempdir()) / "dlp_pass_selftest"

    failed = 0
    for raw in paths:
        source = Path(raw)
        task = DecryptTask(source=source, output=out_dir / source.name, suffix=source.suffix.lower())
        outcome = service.decrypt_file(task, log=lambda message: logger.info("[selftest] %s", message))
        if outcome.success:
            logger.info("[selftest] 解密成功: %s -> %s", source, task.output)
        else:
            failed += 1
            logger.error("[selftest] 解密失败: %s (%s)", source, outcome.error)

    verifier.close()
    service.close()
    if not paths:
        logger.info("[selftest] 未提供待解密文件，仅完成映射加载检查")
    logger.info("[selftest] 结束：%d 个文件，失败 %d 个", len(paths), failed)
    return 1 if failed else 0


def run() -> None:
    """程序入口：加载配置与映射后启动 Qt 图形界面（或执行 --selftest 自检）。"""
    _setup_logging()

    if "--selftest" in sys.argv:
        index = sys.argv.index("--selftest")
        sys.exit(_run_selftest(sys.argv[index + 1:]))

    prepare_high_dpi()
    app = QApplication(sys.argv)
    app.setApplicationName("DLP 解密助手")
    app.setApplicationVersion(dlp_pass.__version__)
    app.setStyle("Fusion")
    app.setFont(QFont(theme.FONT_FAMILY, 10))
    app.setStyleSheet(theme.build_stylesheet())

    _install_exception_hook()

    project_root = Path(__file__).resolve().parents[2]
    config = _load_config(project_root)
    mapping = load_latest(config, project_root)  # 启动约定：读取 mappings/ 中最新版本
    window = MainWindow(config, mapping)
    window.show()
    sys.exit(app.exec())
