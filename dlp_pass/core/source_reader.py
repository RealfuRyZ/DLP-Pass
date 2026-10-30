"""源文件明文读取器。

背景
----
DLP 透明加密驱动只对「白名单进程」返回解密后的数据；发布版 exe 自身不是
白名单进程，直接读取加密文件拿到的是磁盘原始密文。不同厂商的密文形态不同：
亿赛通带 ``%TSD-Header-###%`` 标识串；艾特盾等无任何明文标识，需要按
「后缀应有明文签名缺失 + 密文统计特征」判定（见 :mod:`dlp_pass.core.schemes`）。

委派读取途径（按优先级）
------------------------
1. **伪装白名单进程**（v2.5.1，issue #1）：把映射表给出的授权透明解密进程
   （WINWORD / EXCEL / POWERPNT / wps 等）名复制到内置 stub exe 上运行，
   以 ``<进程名>.exe`` 的镜像名读取——艾特盾这类「只对客户端显式授权进程
   解密」的厂商，cmd / python 读回的是与磁盘一致的密文，只有本途径可行；
2. ``cmd /c type``：通用途径，对 broadly 信任系统进程的厂商（亿赛通等）有效；
3. ``python -c``：兜底途径（路径经环境变量传递，无命令行注入风险）。
   WindowsApps 商店存根在未安装 Python 时以 9009 退出，视为该候选不可用。

本模块在两种运行模式下都能拿到明文流：直接读取判定非密文即返回；
否则依次委派上述途径，全部失败时抛出 :class:`SourceReadError`，
并按「委派字节与磁盘密文完全一致」给出可区分「未授权 / 无驱动」的错误文案。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Iterable, Optional

from ..debug_mode import head_preview, is_debug_enabled, save_bytes
from .schemes import DEFAULT_MARKER, SchemeRegistry, default_registry

_logger = logging.getLogger(__name__)

_CREATE_NO_WINDOW = 0x08000000
# cmd 命令行中的敏感字符（可能触发注入或解析错误，此类路径跳过 cmd 途径）
_CMD_UNSAFE_CHARS = set('&|<>^()%!"\'')
# 委派读取失败时记录的 stderr 摘要长度上限
_STDERR_PREVIEW = 200
# python 兜底的候选命令（WindowsApps 存根未装真实 Python 时返回 9009，逐个尝试）
_PYTHON_CANDIDATES = ("python", "python3", "py")

# 伪装进程会话获取器：进程名 -> 带 read_file(path) 的常驻会话（或 None 表示不可用）
AcquireSession = Callable[[str], Optional[object]]
LogCallback = Callable[[str], None]


class SourceReadError(RuntimeError):
    """无法获得源文件明文流。"""


class SourceReader:
    """读取源文件明文（必要时自动委派白名单进程，对调用方透明）。"""

    def __init__(self, marker: str = DEFAULT_MARKER, registry: SchemeRegistry | None = None):
        # 优先使用注入的判定链（可携带配置多标识串/厂商方案）；
        # 仅给 marker 时保持旧单标识串用法。
        self._registry = registry if registry is not None else default_registry(markers=(marker,))

    def read(
        self,
        path: Path | str,
        process_queue: Iterable[str] = (),
        acquire_session: AcquireSession | None = None,
        log: LogCallback | None = None,
    ) -> bytes:
        """返回源文件明文内容；无法获得明文时抛出 :class:`SourceReadError`。

        ``process_queue`` + ``acquire_session`` 提供时优先委派伪装白名单进程
        读取（艾特盾等细粒度授权厂商的唯一可行途径），cmd / python 降为兜底。
        """
        emit = log or (lambda message: None)
        target = Path(path)
        data = target.read_bytes()
        verdict = self._registry.decide(target.suffix, data, len(data))
        if is_debug_enabled():
            _logger.debug(
                "源文件直读 [%s]: %d 字节 头部 %s 判定 %s/%s（%s）",
                target.name, len(data), head_preview(data),
                verdict.decision.value, verdict.scheme, verdict.detail or "-",
            )
            save_bytes(f"{target.name}.disk.bin", data)
        if verdict.is_cipher is not True:
            return data  # 本进程可信（或文件本就是明文）

        _logger.info("当前进程读到原始密文，委派白名单进程读取明文: %s", target.name)
        identical_to_disk = False
        tried_disguised = False

        def _accept(label: str, delegated: bytes) -> bool:
            """校验委派结果：明文返回 True；记录「与磁盘密文一致」证据。"""
            nonlocal identical_to_disk
            delegated_verdict = self._registry.decide(target.suffix, delegated, len(delegated))
            if is_debug_enabled():
                _logger.debug(
                    "委派 %s 读取 [%s]: %d 字节 头部 %s 判定 %s/%s（%s）",
                    label, target.name, len(delegated), head_preview(delegated),
                    delegated_verdict.decision.value, delegated_verdict.scheme,
                    delegated_verdict.detail or "-",
                )
                save_bytes(f"{target.name}.delegated-{label}.bin", delegated)
            if delegated_verdict.is_cipher is not True:
                return True
            if delegated == data:
                identical_to_disk = True
            return False

        # ① 伪装白名单进程（映射表授权名，艾特盾等细粒度授权厂商的关键途径）
        if acquire_session is not None:
            for proc_name in process_queue:
                tried_disguised = True
                try:
                    session = acquire_session(proc_name)
                except Exception as exc:
                    _logger.warning("伪装进程 [%s] 准备失败: %s", proc_name, exc)
                    continue
                if session is None:
                    continue
                read_file = getattr(session, "read_file", None)
                if read_file is None:
                    continue
                emit(f"尝试伪装白名单进程 [{proc_name}.exe] 读取明文...")
                try:
                    delegated = read_file(target)
                except Exception as exc:
                    _logger.warning("伪装进程 [%s] 读取失败: %s", proc_name, exc)
                    continue
                if not delegated:
                    _logger.debug(
                        "伪装进程 [%s] 读取 [%s]: 未返回数据（跳过该途径）", proc_name, target.name
                    )
                    continue
                if _accept(proc_name, delegated):
                    _logger.info(
                        "已通过伪装进程 %s.exe 获得解密数据（%d 字节）", proc_name, len(delegated)
                    )
                    return delegated

        # ② cmd / ③ python 兜底（对信任系统进程的厂商有效）
        for name, delegate in (("cmd", self._read_via_cmd), ("python", self._read_via_python)):
            try:
                delegated = delegate(target)
            except Exception as exc:
                _logger.warning("委派 %s 读取失败: %s", name, exc)
                continue
            if delegated is None:
                _logger.debug("委派 %s 读取 [%s]: 未返回数据（跳过该途径）", name, target.name)
                continue
            if _accept(name, delegated):
                _logger.info("已通过 %s 获得解密数据（%d 字节）", name, len(delegated))
                return delegated

        raise SourceReadError(self._failure_message(identical_to_disk, tried_disguised))

    @staticmethod
    def _failure_message(identical_to_disk: bool, tried_disguised: bool) -> str:
        """按委派证据区分失败原因，给出可操作的提示。"""
        if identical_to_disk:
            parts = [
                "所有可用读取途径返回的字节与磁盘密文完全一致："
                "本机 DLP 客户端未对这些进程授权透明解密"
            ]
            if tried_disguised:
                parts.append(
                    "（映射队列中的授权进程均已尝试，可能该厂商驱动校验进程数字签名/路径，"
                    "或客户端授权列表与映射表不一致，请核对客户端「透明解密进程」配置）"
                )
            parts.append("；若本机并无 DLP 客户端/驱动（纯密文副本），则无法离线解密。")
            return "".join(parts)
        return "无法通过白名单进程获得解密数据（伪装进程 / cmd / python 委派读取均失败）"

    # ---------------------------------------------------------------- 委派读取

    @staticmethod
    def _read_via_cmd(path: Path) -> bytes | None:
        """通过 cmd（白名单进程）读取；路径含特殊字符或读取失败时返回 None。"""
        text = str(path)
        if any(ch in _CMD_UNSAFE_CHARS for ch in text):
            _logger.warning("路径包含 cmd 特殊字符，跳过 cmd 委派: %s", path)
            return None
        command = ["cmd", "/c", "type", text]  # 参数由 subprocess 安全引用（含空格路径可用）
        if is_debug_enabled():
            _logger.debug("cmd 委派读取: %s", text)
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            _logger.debug(
                "cmd 委派读取失败: 返回码=%d stderr=%s",
                result.returncode, _stderr_preview(result.stderr),
            )
            return None
        if is_debug_enabled():
            _logger.debug("cmd 委派读取完成: 返回码=0 输出 %d 字节", len(result.stdout or b""))
        return result.stdout

    @staticmethod
    def _read_via_python(path: Path) -> bytes | None:
        """通过 python（白名单进程）读取；路径经环境变量传递，无命令行解析风险。

        依次尝试 python / python3 / py：WindowsApps 商店存根在未安装真实 Python
        时以 9009 退出且无 stderr，视为该候选不可用并继续下一个。
        """
        candidates: list[str] = []
        seen: set[str] = set()
        for name in _PYTHON_CANDIDATES:
            found = shutil.which(name)
            if found and found.lower() not in seen:
                seen.add(found.lower())
                candidates.append(found)
        if not candidates:
            _logger.debug("python 委派读取: 未找到 python / python3 / py 可执行文件")
            return None
        env = dict(os.environ, DLP_PASS_SOURCE=str(path))
        for python_exe in candidates:
            if is_debug_enabled():
                _logger.debug("python 委派读取: %s -> %s", python_exe, path)
            try:
                result = subprocess.run(
                    [
                        python_exe, "-c",
                        "import os,sys; sys.stdout.buffer.write(open(os.environ['DLP_PASS_SOURCE'],'rb').read())",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                    creationflags=_CREATE_NO_WINDOW,
                )
            except OSError as exc:
                _logger.debug("python 委派读取失败: %s 无法启动（%s）", python_exe, exc)
                continue
            if result.returncode == 9009:
                _logger.debug(
                    "python 委派读取: %s 返回 9009（商店存根或未安装），跳过该候选", python_exe
                )
                continue
            if result.returncode != 0:
                _logger.debug(
                    "python 委派读取失败: %s 返回码=%d stderr=%s",
                    python_exe, result.returncode, _stderr_preview(result.stderr),
                )
                continue
            if is_debug_enabled():
                _logger.debug(
                    "python 委派读取完成: %s 返回码=0 输出 %d 字节",
                    python_exe, len(result.stdout or b""),
                )
            return result.stdout
        return None


def _stderr_preview(stderr: bytes | None) -> str:
    """子进程 stderr 的单行摘要（调试日志用，截断避免噪声）。"""
    if not stderr:
        return "(无)"
    text = stderr.decode("utf-8", "replace").strip().replace("\n", " | ")
    return text[:_STDERR_PREVIEW] + ("…" if len(text) > _STDERR_PREVIEW else "")
