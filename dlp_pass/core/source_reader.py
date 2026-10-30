"""源文件明文读取器。

背景
----
DLP 透明加密驱动只对「白名单进程」返回解密后的数据；发布版 exe 自身不是
白名单进程，直接读取加密文件拿到的是磁盘原始密文。不同厂商的密文形态不同：
亿赛通带 ``%TSD-Header-###%`` 标识串；艾特盾等无任何明文标识，需要按
「后缀应有明文签名缺失 + 密文统计特征」判定（见 :mod:`dlp_pass.core.schemes`）。
本模块负责在两种运行模式下都能拿到明文流：

1. 直接读取：若内容判定不为密文，说明本进程可信（或文件本就是
   明文），直接返回；
2. 委派读取：若读到原始密文，则委派白名单进程重新读取：
   - ``cmd /c type`` ：通用途径（任何 Windows 均有 cmd.exe），已验证对
     二进制内容字节级一致；
   - ``python -c``   ：兜底途径（路径经环境变量传递，无命令行注入风险），
      用于路径含 cmd 特殊字符或 cmd 不可用的场景。
3. 委派后仍为密文时抛出 :class:`SourceReadError`，让任务以明确错误结束。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from ..debug_mode import head_preview, is_debug_enabled, save_bytes
from .schemes import DEFAULT_MARKER, SchemeRegistry, default_registry

_logger = logging.getLogger(__name__)

_CREATE_NO_WINDOW = 0x08000000
# cmd 命令行中的敏感字符（可能触发注入或解析错误，此类路径跳过 cmd 途径）
_CMD_UNSAFE_CHARS = set('&|<>^()%!"\'')
# 委派读取失败时记录的 stderr 摘要长度上限
_STDERR_PREVIEW = 200


class SourceReadError(RuntimeError):
    """无法获得源文件明文流。"""


class SourceReader:
    """读取源文件明文（必要时自动委派白名单进程，对调用方透明）。"""

    def __init__(self, marker: str = DEFAULT_MARKER, registry: SchemeRegistry | None = None):
        # 优先使用注入的判定链（可携带配置多标识串/厂商方案）；
        # 仅给 marker 时保持旧单标识串用法。
        self._registry = registry if registry is not None else default_registry(markers=(marker,))

    def read(self, path: Path | str) -> bytes:
        """返回源文件明文内容；无法获得明文时抛出 :class:`SourceReadError`。"""
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
        for name, delegate in (("cmd", self._read_via_cmd), ("python", self._read_via_python)):
            try:
                delegated = delegate(target)
            except Exception as exc:
                _logger.warning("委派 %s 读取失败: %s", name, exc)
                continue
            if delegated is None:
                _logger.debug("委派 %s 读取 [%s]: 未返回数据（跳过该途径）", name, target.name)
                continue
            delegated_verdict = self._registry.decide(target.suffix, delegated, len(delegated))
            if is_debug_enabled():
                _logger.debug(
                    "委派 %s 读取 [%s]: %d 字节 头部 %s 判定 %s/%s（%s）",
                    name, target.name, len(delegated), head_preview(delegated),
                    delegated_verdict.decision.value, delegated_verdict.scheme,
                    delegated_verdict.detail or "-",
                )
                save_bytes(f"{target.name}.delegated-{name}.bin", delegated)
            if delegated_verdict.is_cipher is not True:
                _logger.info("已通过 %s 获得解密数据（%d 字节）", name, len(delegated))
                return delegated
        raise SourceReadError("无法通过白名单进程获得解密数据（cmd / python 委派读取均失败）")

    # ---------------------------------------------------------------- 密文判定

    def _is_cipher(self, data: bytes, suffix: str) -> bool:
        """磁盘原始数据是否呈现密文特征（无法判定时视为明文）。"""
        return self._registry.decide(suffix, data, len(data)).is_cipher is True

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
        """通过 python（白名单进程）读取；路径经环境变量传递，无命令行解析风险。"""
        python_exe = shutil.which("python") or shutil.which("python3")
        if not python_exe:
            _logger.debug("python 委派读取: 未找到 python / python3 可执行文件")
            return None
        env = dict(os.environ, DLP_PASS_SOURCE=str(path))
        if is_debug_enabled():
            _logger.debug("python 委派读取: %s -> %s", python_exe, path)
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
        if result.returncode != 0:
            _logger.debug(
                "python 委派读取失败: 返回码=%d stderr=%s",
                result.returncode, _stderr_preview(result.stderr),
            )
            return None
        if is_debug_enabled():
            _logger.debug("python 委派读取完成: 返回码=0 输出 %d 字节", len(result.stdout or b""))
        return result.stdout


def _stderr_preview(stderr: bytes | None) -> str:
    """子进程 stderr 的单行摘要（调试日志用，截断避免噪声）。"""
    if not stderr:
        return "(无)"
    text = stderr.decode("utf-8", "replace").strip().replace("\n", " | ")
    return text[:_STDERR_PREVIEW] + ("…" if len(text) > _STDERR_PREVIEW else "")
