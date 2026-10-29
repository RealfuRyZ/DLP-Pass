"""密文检测：判断磁盘上的文件是否仍被 DLP 加密（处于密文状态）。

原理
----
PowerShell 进程不在 DLP 白名单中，读取到的是磁盘上的原始数据。拿到原始
头部字节后，交由 :mod:`dlp_pass.core.schemes` 的可扩展判定链按文件后缀
做「内容级」识别（标识串 / 明文签名缺失 / ECB 重复块指纹等，见包文档）。

原实现只在文件头查找单一标识串，能覆盖亿赛通但对无标识串的厂商（艾特盾，
issue #1 等）会漏判；现由方案链统一处理，新厂商接入不动本模块。

返回值约定：``True`` = 仍为密文；``False`` = 已是明文；``None`` = 无法判定。

实现
----
- :class:`PowerShellOracle`：常驻一个 PowerShell 进程，通过 stdin/stdout
  行协议（base64 路径 -> 头部字节的 hex）批量检测，首次启动约 0.2s，
  之后单文件耗时不足 1ms，适合文件夹批量扫描。
- 一次性回退：若常驻进程不可用，则以同样的窗口规则单独调用 PowerShell。
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
from .schemes import HEAD_WINDOW, Decision, registry_from_config

_logger = logging.getLogger(__name__)

_CREATE_NO_WINDOW = 0x08000000

# 读取的头部窗口：覆盖各方案所需字节（标识串 / 偏移魔数 / ECB 统计指纹）。
_HEAD_WINDOW = HEAD_WINDOW

# PowerShell 行协议：收到 base64 路径，回读头部字节并回传其 base16（hex）；
# 空行回传空串；读失败回传 'E'。检测与格式判定解耦，脚本本身不含任何厂商标识。
_ORACLE_SCRIPT = r"""$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::ASCII
$window = __WINDOW__
while ($true) {
    $line = [Console]::In.ReadLine()
    if ($null -eq $line) { break }
    if ($line -eq 'QUIT') { [Console]::Out.WriteLine('BYE'); [Console]::Out.Flush(); break }
    try {
        $path = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($line))
        $fs = [System.IO.File]::Open($path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
        try { $buffer = New-Object byte[] $window; $read = $fs.Read($buffer, 0, $window) } finally { $fs.Close() }
        if ($read -le 0) { $read = 0 }
        $hex = [System.BitConverter]::ToString($buffer, 0, $read).Replace('-','')
        [Console]::Out.WriteLine($hex)
    } catch {
        [Console]::Out.WriteLine('E')
    }
    [Console]::Out.Flush()
}
""".replace("__WINDOW__", str(_HEAD_WINDOW))


_TEMP_SUFFIX = ".pass"


def _effective_suffix(target: Path) -> str:
    """判定所用的真实后缀。

    原地解密校验对象是 ``xxx.xls.pass`` 临时文件：``Path.suffix`` 会取到
    ``.pass``，使格式签名方案查不到证据、错误退化到 ECB 统计指纹——合法
    明文 Office 文件（空白 BIFF 记录含大量重复非零块）会被误判为密文。
    此处剥离 ``.pass`` 外层取内层后缀，让强证据方案正常定案。
    """
    suffix = target.suffix.lower()
    if suffix == _TEMP_SUFFIX:
        inner = Path(target.stem).suffix.lower()
        if inner:
            return inner
    return suffix


class PowerShellOracle:
    """常驻 PowerShell 头部读取进程（线程安全）。"""

    def __init__(self, timeout: float = 10.0):
        self._timeout = timeout
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._responses: queue.Queue[str | None] | None = None
        self._reader: threading.Thread | None = None

    def query(self, path: Path) -> bytes | None:
        """读取文件头部原始字节；读取失败返回 ``None``（交由上层回退处理）。"""
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
            if reply is None or reply == "E":
                if reply is None:
                    self._shutdown_locked()
                return None
            try:
                return bytes.fromhex(reply)
            except ValueError:
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
                "-ExecutionPolicy", "Bypass", "-Command", _ORACLE_SCRIPT,
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
        self._timeout = config.powershell_timeout
        self._registry = registry_from_config(config)
        self._oracle = PowerShellOracle(timeout=self._timeout)

    def is_encrypted(self, path: Path | str) -> bool | None:
        """``True`` 仍为密文 / ``False`` 已是明文 / ``None`` 无法判定。"""
        target = Path(path)
        if not target.exists():
            return True
        data = self._oracle.query(target)
        if data is None:
            data = self._read_with_one_shot(target)
        if data is None:
            return None
        verdict = self._registry.decide(_effective_suffix(target), data, target.stat().st_size)
        if verdict.decision is Decision.CIPHER:
            _logger.info("判定为密文 [%s]: %s (%s)", target.name, verdict.scheme, verdict.detail)
        return verdict.is_cipher

    def close(self) -> None:
        self._oracle.close()

    def _read_with_one_shot(self, path: Path) -> bytes | None:
        """兜底读取：单独启动 PowerShell，以同样的窗口读取文件头部原始字节。"""
        ps_command = (
            "try { "
            "$fs = [System.IO.File]::Open($env:DLP_PASS_TARGET, [System.IO.FileMode]::Open, "
            "[System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite); "
            f"try {{ $buffer = New-Object byte[] {_HEAD_WINDOW}; $read = $fs.Read($buffer, 0, {_HEAD_WINDOW}) }} finally {{ $fs.Close() }}; "
            "if ($read -le 0) { $read = 0 }; "
            "[Console]::Out.Write([System.BitConverter]::ToString($buffer, 0, $read).Replace('-','')) "
            "} catch { exit 2 }"
        )
        env = dict(os.environ)
        env["DLP_PASS_TARGET"] = str(path)
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_command],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=self._timeout,
                creationflags=_CREATE_NO_WINDOW,
            )
        except Exception as exc:
            _logger.error("PowerShell 兜底校验执行失败: %s", exc)
            return None
        if result.returncode != 0 or result.stdout is None:
            return None
        text = result.stdout.decode("ascii", "ignore").strip()
        try:
            return bytes.fromhex(text)
        except ValueError:
            _logger.warning("PowerShell 兜底读取输出异常（无法解析为十六进制头部字节）: %s", path.name)
            return None
