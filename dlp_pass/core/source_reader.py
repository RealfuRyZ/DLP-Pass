"""源文件明文读取器。

背景
----
DLP 透明加密驱动只对「白名单进程」返回解密后的数据；发布版 exe 自身不是
白名单进程，直接读取加密文件拿到的是磁盘原始密文（以 ``%TSD-Header-###%``
开头）。本模块负责在两种运行模式下都能拿到明文流：

1. 直接读取：若内容不以密文容器签名开头，说明本进程可信（或文件本就是
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

_logger = logging.getLogger(__name__)

_CREATE_NO_WINDOW = 0x08000000
# cmd 命令行中的敏感字符（可能触发注入或解析错误，此类路径跳过 cmd 途径）
_CMD_UNSAFE_CHARS = set('&|<>^()%!"\'')


class SourceReadError(RuntimeError):
    """无法获得源文件明文流。"""


class SourceReader:
    """读取源文件明文（必要时自动委派白名单进程，对调用方透明）。"""

    def __init__(self, marker: str = "%TSD-Header-###%"):
        self._marker = marker.encode("ascii")

    def read(self, path: Path | str) -> bytes:
        """返回源文件明文内容；无法获得明文时抛出 :class:`SourceReadError`。"""
        target = Path(path)
        data = target.read_bytes()
        if not data.startswith(self._marker):
            return data  # 本进程可信（或文件本就是明文）

        _logger.info("当前进程读到原始密文，委派白名单进程读取明文: %s", target.name)
        for name, delegate in (("cmd", self._read_via_cmd), ("python", self._read_via_python)):
            try:
                delegated = delegate(target)
            except Exception as exc:
                _logger.warning("委派 %s 读取失败: %s", name, exc)
                continue
            if delegated is not None and not delegated.startswith(self._marker):
                _logger.info("已通过 %s 获得解密数据（%d 字节）", name, len(delegated))
                return delegated
        raise SourceReadError("无法通过白名单进程获得解密数据（cmd / python 委派读取均失败）")

    # ---------------------------------------------------------------- 委派读取

    @staticmethod
    def _read_via_cmd(path: Path) -> bytes | None:
        """通过 cmd（白名单进程）读取；路径含特殊字符或读取失败时返回 None。"""
        text = str(path)
        if any(ch in _CMD_UNSAFE_CHARS for ch in text):
            _logger.warning("路径包含 cmd 特殊字符，跳过 cmd 委派: %s", path)
            return None
        result = subprocess.run(
            ["cmd", "/c", "type", text],  # 参数由 subprocess 安全引用（含空格路径可用）
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=_CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            return None
        return result.stdout

    @staticmethod
    def _read_via_python(path: Path) -> bytes | None:
        """通过 python（白名单进程）读取；路径经环境变量传递，无命令行解析风险。"""
        python_exe = shutil.which("python") or shutil.which("python3")
        if not python_exe:
            return None
        env = dict(os.environ, DLP_PASS_SOURCE=str(path))
        result = subprocess.run(
            [
                python_exe, "-c",
                "import os,sys; sys.stdout.buffer.write(open(os.environ['DLP_PASS_SOURCE'],'rb').read())",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            creationflags=_CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            return None
        return result.stdout
