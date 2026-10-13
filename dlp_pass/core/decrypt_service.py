"""核心解密服务：与界面完全解耦，可独立在命令行 / 测试中调用。

流程（与原脚本保持一致）
-----------------------
1. 用当前 Python 进程读取源文件，得到已解密的明文内存流；
2. 从本地映射表（mappings/ 当前版本）取该后缀对应的伪装进程队列（主进程 -> 备用进程）；
3. 依次构建/复用伪装进程，将明文流经 stdin 写入 ``*.pass`` 临时文件；
4. 将 ``*.pass`` 重命名为最终文件（等价于原脚本的 ``move /y``）；
5. 用 PowerShell 校验输出文件是否仍含 TSD-Header 密文头，
   若仍为密文则删除输出并尝试下一个备用进程。

输出模式（见 :mod:`dlp_pass.core.output_mode`）
--------------------------------------------
- 输出到指定目录：``task.output`` 位于输出目录内，保留相对目录结构，原始文件不动；
- 原地解密覆盖：``task.in_place=True`` 且 ``task.output == task.source``。
  此时先校验临时 ``*.pass`` 文件已是明文，再重命名覆盖原文件，
  校验不通过时原文件绝不被动（重命名不改变内容，故无需二次校验）。
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
from pathlib import Path
from typing import Callable

from .mapping_loader import MappingSet
from .models import DecryptOutcome, DecryptTask
from .source_reader import SourceReader
from .verifier import EncryptionVerifier
from .writer_factory import DisguisedWriterFactory

_logger = logging.getLogger(__name__)

_CREATE_NO_WINDOW = 0x08000000

LogCallback = Callable[[str], None]
CancelCallback = Callable[[], bool]


class DecryptService:
    """单个文件的解密导出服务。"""

    def __init__(
        self,
        mapping: MappingSet,
        verifier: EncryptionVerifier,
        writer_factory: DisguisedWriterFactory,
        source_reader: SourceReader | None = None,
    ):
        self._mapping = mapping
        self._verifier = verifier
        self._writers = writer_factory
        # 源读取器：发布版进程非 DLP 白名单时自动委派 cmd / python 读取明文
        self._reader = source_reader or SourceReader()

    def decrypt_file(
        self,
        task: DecryptTask,
        log: LogCallback | None = None,
        is_cancelled: CancelCallback | None = None,
    ) -> DecryptOutcome:
        """执行一次解密任务，返回结果（不抛异常）。"""
        emit = log or (lambda message: None)
        cancelled = is_cancelled or (lambda: False)
        outcome = DecryptOutcome(source=task.source, success=False)

        emit(f"正在读取源文件内存流: {task.source.name}")
        try:
            raw_bytes = self._reader.read(task.source)
        except Exception as exc:
            outcome.error = f"内存读取失败: {exc}"
            emit(outcome.error)
            return outcome
        emit(f"内存读取成功，文件大小: {len(raw_bytes)} 字节")

        process_queue = self._mapping.get_process_queue(task.suffix)
        for index, proc_name in enumerate(process_queue, 1):
            if cancelled():
                outcome.cancelled = True
                outcome.error = "已取消"
                return outcome
            emit(f"尝试第 {index} 个白名单进程 [{proc_name}.exe] 进行导出...")
            success, note = self._export_with_process(
                raw_bytes, proc_name, task.output, emit, in_place=task.in_place
            )
            outcome.attempts.append(f"{proc_name}: {note}")
            if success:
                outcome.success = True
                outcome.output = task.output
                outcome.process_name = proc_name
                outcome.sha256 = hashlib.sha256(raw_bytes).hexdigest()
                emit(f"[+] 校验通过！成功使用进程 [{proc_name}.exe] 导出无加密的明文文件!")
                emit(f"最终文件路径 : {task.output}")
                emit(f"文件 SHA256  : {outcome.sha256}")
                return outcome
            emit(f"进程 [{proc_name}.exe] 尝试落盘失败或未通过校验，尝试下一个备用进程...")

        outcome.error = "所有尝试的白名单进程均未能完成落盘（或导出文件均包含 TSD-Header 标识）！"
        emit(outcome.error)
        return outcome

    # ------------------------------------------------------------------ 内部

    def _export_with_process(
        self,
        raw_bytes: bytes,
        proc_name: str,
        final_file: Path,
        emit: LogCallback,
        in_place: bool = False,
    ) -> tuple[bool, str]:
        try:
            writer_exe = self._writers.get_writer(proc_name)
        except Exception as exc:
            emit(f"打包伪装进程 [{proc_name}] 失败: {exc}")
            return False, f"构建失败: {exc}"

        pass_file = final_file.parent / f"{final_file.name}.pass"
        try:
            final_file.parent.mkdir(parents=True, exist_ok=True)
            pass_file.unlink(missing_ok=True)
            proc = subprocess.Popen(
                [str(writer_exe), str(pass_file)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=_CREATE_NO_WINDOW,
            )
            proc.communicate(input=raw_bytes)
        except Exception as exc:
            return False, f"落盘失败: {exc}"

        if not pass_file.exists():
            return False, "伪装进程未产生输出文件"

        if in_place:
            # 原地覆盖：先校验临时文件为明文再替换原文件（重命名不改变内容），
            # 避免把密文临时文件覆盖到原始路径导致原文件损坏。
            if self._verifier.is_encrypted(pass_file) is True:
                emit("校验结果: 临时导出文件仍包含 TSD-Header 密文标头，原文件保持不变")
                try:
                    pass_file.unlink(missing_ok=True)
                except OSError:
                    pass
                return False, "导出仍为密文（原文件未改动）"
            if not self._move_into_place(pass_file, final_file):
                return False, "覆盖原文件失败"
            return True, "校验通过（原地覆盖）"

        if not self._move_into_place(pass_file, final_file):
            return False, "输出文件重命名失败"

        if self._verifier.is_encrypted(final_file) is True:
            emit(f"校验结果: 进程 [{proc_name}.exe] 导出的文件仍包含 TSD-Header 密文标头！")
            try:
                final_file.unlink(missing_ok=True)
            except OSError:
                pass
            return False, "输出仍为密文"
        return True, "校验通过"

    @staticmethod
    def _move_into_place(pass_file: Path, final_file: Path) -> bool:
        """把临时 ``*.pass`` 文件移动到目标路径（等价原脚本的 ``move /y``）。"""
        try:
            os.replace(pass_file, final_file)
            return final_file.exists()
        except OSError:
            # 与原始脚本一致的重命名兜底方案
            subprocess.run(
                f'cmd /c move /y "{pass_file}" "{final_file}"',
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_CREATE_NO_WINDOW,
            )
            return final_file.exists() and not pass_file.exists()
