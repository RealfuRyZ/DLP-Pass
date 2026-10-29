"""密文检测：判断磁盘上的文件是否仍被 DLP 加密（处于密文状态）。

原理
----
PowerShell 进程不在 DLP 白名单中，读取到的是磁盘上的原始数据。拿到原始
文件头字节后，交由 :mod:`dlp_pass.core.formats` 按文件后缀做「内容级」判定：

- 亿赛通 CDGC：密文头带明文标识串 ``%TSD-Header-###%``，命中即密文；
- 艾特盾等（issue #1）：密文不含任何明文标识，但受管 Office 文档的密文头
  缺少该格式应有的明文签名（ZIP / OLE 魔数），据此判定；未知后缀则退化为
  ECB 分组重复指纹。

原实现只在文件头查找标识串，能覆盖亿赛通但对无标识串的艾特盾密文会漏判
（读到的原始密文恰好不含 TSD 字样，被误认为明文）。本版本在保持亿赛通行为
不变的前提下，用 PowerShell 回传头部字节、Python 侧做签名比对来扩展识别。

返回值约定：``True`` = 仍为密文；``False`` = 已是明文；``None`` = 无法判定。

实现
----
- :class:`PowerShellOracle`：常驻一个 PowerShell 进程，通过 stdin/stdout
  行协议（base64 路径 -> 头部字节的 base16）批量检测，首次启动约 0.2s，
  之后单文件耗时不足 1ms，适合文件夹批量扫描。
- 一次性回退：若常驻进程不可用，则以同样的字节读取规则单独调用 PowerShell。
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
from . import formats

_logger = logging.getLogger(__name__)

_CREATE_NO_WINDOW = 0x08000000

# 读取的头部窗口：需覆盖最宽签名（含 ECB 指纹采样窗口所需的前 16KB 足够短签名比对，
# 指纹部分在 Python 侧对同一段头部字节复用）。
_HEAD_WINDOW = formats.ECB_WINDOW  # 16384

# PowerShell 行协议：收到 base64 路径，回读头部字节并回传其 base16（hex）；
# 空行回传空串；读失败回传 'E'。检测与格式判定解耦，脚本本身不含任何厂商标识。
_ORACLE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
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
        self._marker = config.encryption_marker
        self._timeout = config.powershell_timeout
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
        return formats.is_cipher_content(data, target.suffix, self._marker)

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
