"""QThread 后台工作器：文件扫描与批量解密。

所有耗时操作（PowerShell 密文检测、子进程读取明文、PyInstaller 动态构建与
子进程写盘）都在 QThread 中执行，通过 Qt 信号与界面线程通信，
保证 UI 主线程绝对不被阻塞。

映射查询（后缀 -> 伪装进程）来自本地内存/文件（见 core.mapping_loader），
直接在界面线程同步完成，无需后台线程。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from pathlib import Path

from .qt_compat import QThread, pyqtSignal

from ..core.decrypt_service import DecryptService
from ..core.file_collector import collect_entries
from ..core.models import DecryptTask, make_item_id
from ..core.verifier import EncryptionVerifier

_logger = logging.getLogger(__name__)


class ScanWorker(QThread):
    """后台扫描拖入的文件 / 文件夹并检测加密状态。

    - 每个文件只发一个合并事件（含检测结果），大幅减少界面事件量；
    - 每检测 100 个文件上报一次进度；
    - 单次扫描设有数量上限，避免误拖含虚拟环境等的大目录导致超长任务。
    """

    entry_ready = pyqtSignal(dict)  # {item_id, name, size, source, relative_output, suffix, encrypted}
    progress = pyqtSignal(int)  # 已检测文件数
    finished_scan = pyqtSignal(int, bool)  # 新增条目数, 是否因达到上限而截断
    failed = pyqtSignal(str)

    MAX_FILES = 20000

    def __init__(self, verifier: EncryptionVerifier, paths: Sequence[str], parent=None):
        super().__init__(parent)
        self._verifier = verifier
        self._paths = list(paths)
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        added = 0
        truncated = False
        try:
            # 多取 1 个用于判断是否发生截断
            for index, collected in enumerate(collect_entries(self._paths, limit=self.MAX_FILES + 1)):
                if self._cancel.is_set():
                    break
                if index >= self.MAX_FILES:
                    truncated = True
                    break
                encrypted = self._verifier.is_encrypted(collected.source)
                self.entry_ready.emit(
                    {
                        "item_id": make_item_id(collected.source),
                        "name": collected.source.name,
                        "size": self._safe_size(collected.source),
                        "source": collected.source,
                        "relative_output": collected.relative_output,
                        "suffix": collected.suffix,
                        "encrypted": encrypted,
                    }
                )
                added += 1
                if added % 100 == 0:
                    self.progress.emit(added)
        except Exception as exc:
            _logger.exception("扫描任务异常")
            self.failed.emit(str(exc))
        self.finished_scan.emit(added, truncated)

    @staticmethod
    def _safe_size(path: Path) -> int | None:
        try:
            return path.stat().st_size
        except OSError:
            return None


def group_tasks_by_suffix(tasks: Sequence[DecryptTask]) -> list[DecryptTask]:
    """按文件后缀分组排序：同后缀任务连续排列，组内保持原有顺序（稳定排序）。

    仅影响后台处理顺序（前台文件列表的展示顺序不变）；后缀连续使核心服务
    可以复用同一个常驻伪装进程，避免逐文件反复创建 / 销毁伪装进程的开销。
    """
    return sorted(tasks, key=lambda task: task.suffix.lower())


class DecryptWorker(QThread):
    """后台批量解密：按后缀分组排序后逐文件调用核心 DecryptService。

    同后缀任务连续处理：核心服务对每个后缀只启动一次伪装进程并在组内复用，
    组处理完（或后缀切换 / 取消）后再关闭，避免逐文件反复创建、销毁进程。
    """

    task_started = pyqtSignal(str)  # item_id
    task_finished = pyqtSignal(str, str, str)  # item_id, status(success/failed/cancelled), note
    progress = pyqtSignal(int, int, int, int)  # done, total, success, failed
    log = pyqtSignal(str, str)  # message, level
    finished_all = pyqtSignal(dict)  # {total, done, success, failed}

    def __init__(self, service: DecryptService, tasks: Sequence[DecryptTask], parent=None):
        super().__init__(parent)
        self._service = service
        self._tasks = list(tasks)
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        total = len(self._tasks)
        done = success = failed = 0
        try:
            for task in group_tasks_by_suffix(self._tasks):
                item_id = task.item_id
                if self._cancel.is_set():
                    self.task_finished.emit(item_id, "cancelled", "已取消")
                    done += 1
                    self.progress.emit(done, total, success, failed)
                    continue

                self.task_started.emit(item_id)
                try:
                    outcome = self._service.decrypt_file(
                        task,
                        log=lambda message: self.log.emit(message, "info"),
                        is_cancelled=self._cancel.is_set,
                    )
                except Exception as exc:
                    _logger.exception("解密任务异常")
                    self.task_finished.emit(item_id, "failed", f"内部异常: {exc}")
                    done += 1
                    failed += 1
                    self.progress.emit(done, total, success, failed)
                    continue

                if outcome.cancelled:
                    self.task_finished.emit(item_id, "cancelled", "已取消")
                    done += 1
                elif outcome.success:
                    success += 1
                    done += 1
                    sha_prefix = (outcome.sha256 or "")[:12]
                    self.task_finished.emit(
                        item_id, "success", f"进程 {outcome.process_name}.exe · SHA256 {sha_prefix}…"
                    )
                else:
                    failed += 1
                    done += 1
                    self.task_finished.emit(item_id, "failed", outcome.error or "导出失败")
                self.progress.emit(done, total, success, failed)
        finally:
            # 批量结束 / 取消 / 异常：关闭常驻伪装进程，避免进程残留
            self._service.close()

        self.finished_all.emit({"total": total, "done": done, "success": success, "failed": failed})
