"""密文检测：判断磁盘上的文件是否仍被 DLP 加密（含密文容器签名）。

原理（与原脚本一致）
------------------
PowerShell 进程不在 DLP 白名单中，读取到的是磁盘上的原始数据；
若原始数据开头存在密文容器签名 ``%TSD-Header-###%``，说明文件仍处于加密状态。

说明：原脚本按「前 5 行是否包含 TSD-Header 字样」进行匹配，容易对
注释中含该字样的明文源码误报；这里改为在原始数据前 64 字节内匹配
完整容器签名，检测目标不变（容器签名必然位于文件开头），且不再误报。

实现
----
- :class:`PowerShellOracle`：常驻一个 PowerShell 进程，通过
  stdin/stdout 行协议（base64 路径 -> 0/1/E）批量检测，首次启动约 0.2s，
  之后单文件耗时不足 1ms，适合文件夹批量扫描。
- 一次性回退：若常驻进程不可用，则以同样的签名规则单独调用 PowerShell 检测。

返回值约定：``True`` = 仍为密文；``False`` = 已是明文；``None`` = 无法判定。
"""

from __future__ import annotations

import base64
import logging
import os
import queue
import subprocess
import threading
from pathlib import Path

from ..config import AppConfig

_logger = logging.getLogger(__name__)

_CREATE_NO_WINDOW = 0x08000000

_ORACLE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::ASCII
while ($true) {
    $line = [Console]::In.ReadLine()
    if ($null -eq $line) { break }
    if ($line -eq 'QUIT') { [Console]::Out.WriteLine('BYE'); [Console]::Out.Flush(); break }
    try {
        $path = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($line))
        $fs = [System.IO.File]::Open($path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
        try { $buffer = New-Object byte[] 64; $read = $fs.Read($buffer, 0, 64) } finally { $fs.Close() }
        $text = [System.Text.Encoding]::ASCII.GetString($buffer, 0, $read)
        if ($text.Contains('__MARKER__')) { [Console]::Out.WriteLine('1') } else { [Console]::Out.WriteLine('0') }
    } catch {
        [Console]::Out.WriteLine('E')
    }
    [Console]::Out.Flush()
}
"""


class PowerShellOracle:
    """常驻 PowerShell 检测进程（线程安全）。"""

    def __init__(self, marker: str, timeout: float = 10.0):
        self._timeout = timeout
        self._script = _ORACLE_SCRIPT.replace("__MARKER__", marker)
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._responses: queue.Queue[str | None] | None = None
        self._reader: threading.Thread | None = None

    def query(self, path: Path) -> bool | None:
        """检测单个文件；检测失败返回 ``None``（交由上层回退处理）。"""
        with self._lock:
            try:
                self._ensure_started()
                token = base64.b64encode(os.fsencode(str(path))).decode("ascii")
                assert self._proc is not None and self._proc.stdin is not None
                self._proc.stdin.write(token + "\n")
                self._proc.stdin.flush()
                reply = self._responses.get(timeout=self._timeout)  # type: ignore[union-attr]
            except Exception as exc:
                _logger.warning("PowerShell 常驻检测异常: %s", exc)
                self._shutdown_locked()
                return None
            if reply == "1":
                return True
            if reply == "0":
                return False
            if reply is None:
                self._shutdown_locked()
            return None

    def close(self) -> None:
        with self._lock:
            self._shutdown_locked()

    # ------------------------------------------------------------------ 内部

    def _ensure_started(self) -> None:
        if self._proc is not None and self._proc.poll() is None and self._reader is not None and self._reader.is_alive():
            return
        self._shutdown_locked()
        self._proc = subprocess.Popen(
            [
                "powershell", "-NoProfile", "-NoLogo", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-Command", self._script,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="ascii",
            creationflags=_CREATE_NO_WINDOW,
        )
        self._responses = queue.Queue()
        self._reader = threading.Thread(
            target=self._read_loop, args=(self._proc, self._responses), daemon=True
        )
        self._reader.start()

    @staticmethod
    def _read_loop(proc: subprocess.Popen, responses: queue.Queue) -> None:
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                responses.put(line.strip())
        except (OSError, ValueError):
            pass
        responses.put(None)

    def _shutdown_locked(self) -> None:
        proc = self._proc
        self._proc = None
        self._reader = None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.kill()
        except OSError:
            pass
        for stream in (proc.stdin, proc.stdout):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass


class EncryptionVerifier:
    """对外统一的密文检测入口。"""

    def __init__(self, config: AppConfig):
        self._marker = config.encryption_marker
        self._timeout = config.powershell_timeout
        self._oracle = PowerShellOracle(self._marker, timeout=self._timeout)

    def is_encrypted(self, path: Path | str) -> bool | None:
        """``True`` 仍为密文 / ``False`` 已是明文 / ``None`` 无法判定。"""
        target = Path(path)
        if not target.exists():
            return True
        result = self._oracle.query(target)
        if result is not None:
            return result
        return self._check_with_one_shot(target)

    def close(self) -> None:
        self._oracle.close()

    def _check_with_one_shot(self, path: Path) -> bool | None:
        """兜底检测：单独启动 PowerShell，以同样的签名规则读取文件开头。"""
        ps_command = (
            "$fs = [System.IO.File]::Open($env:DLP_PASS_TARGET, [System.IO.FileMode]::Open, "
            "[System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite); "
            "try { $buffer = New-Object byte[] 64; $read = $fs.Read($buffer, 0, 64) } finally { $fs.Close() }; "
            "$text = [System.Text.Encoding]::ASCII.GetString($buffer, 0, $read); "
            f"if ($text.Contains('{self._marker}')) {{ exit 1 }} else {{ exit 0 }}"
        )
        env = dict(os.environ)
        env["DLP_PASS_TARGET"] = str(path)
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_command],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=self._timeout,
                creationflags=_CREATE_NO_WINDOW,
            )
        except Exception as exc:
            _logger.error("PowerShell 兜底校验执行失败: %s", exc)
            return None
        if result.returncode == 1:
            _logger.warning("校验检测到 %s 标识，文件依旧处于加密状态: %s", self._marker, path.name)
            return True
        if result.returncode == 0:
            return False
        return None
