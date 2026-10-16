"""伪装白名单进程源码模板（不参与应用导入，勿在业务代码中 import）。

用途
----
- 开发模式：:class:`~dlp_pass.core.writer_factory.DisguisedWriterFactory`
  复制本文件并用 PyInstaller 打包为 ``<进程名>.exe``（如 notepad.exe），
  运行时从 stdin 读取明文数据并落盘；
- 发布构建：``packaging/build_exe.py`` 预构建为 ``writer_stub.exe`` 并随
  主程序捆绑，运行时应目标进程名复制释放，无需目标机器安装 PyInstaller。

运行协议（与 :class:`dlp_pass.core.decrypt_service._WriterSession` 对应）
------------------------------------------------------------------------
- 不带参数：常驻批处理模式（批量解密时同一进程连续处理多个文件）。
  stdin 每条命令为一行 ``<base64 路径> <字节数>``，其后紧跟该数量的原始数据；
  每完成一个文件向 stdout 输出一行 ``OK``，失败输出 ``ERR <base64 消息>``；
  stdin 关闭（EOF）后进程退出。
- 带参数（``argv[1]`` = 输出文件路径）：一次性模式（兼容旧调用），
  数据从 stdin 读取到底后写入 ``argv[1]``。
"""

import base64
import os
import sys
from pathlib import Path


def _write_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _read_exact(stream, size: int) -> bytes:
    """从字节流读取恰好 ``size`` 个字节，不足时抛出 EOFError。"""
    chunks = []
    remaining = size
    while remaining > 0:
        chunk = stream.read(min(remaining, 1 << 20))
        if not chunk:
            raise EOFError("stdin 在数据读取完成前关闭")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def run_one_shot() -> int:
    _write_file(Path(sys.argv[1]), sys.stdin.buffer.read())
    return 0


def run_batch() -> int:
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    while True:
        line = stdin.readline()
        if not line:
            return 0
        try:
            token, size_text = line.split()
            target = Path(os.fsdecode(base64.b64decode(token)))
            data = _read_exact(stdin, int(size_text))
            _write_file(target, data)
            stdout.write(b"OK\n")
        except Exception as exc:
            message = base64.b64encode(str(exc).encode("utf-8", "replace"))
            stdout.write(b"ERR " + message + b"\n")
        stdout.flush()


def main() -> int:
    if len(sys.argv) >= 2:
        return run_one_shot()
    return run_batch()


if __name__ == "__main__":
    sys.exit(main())
