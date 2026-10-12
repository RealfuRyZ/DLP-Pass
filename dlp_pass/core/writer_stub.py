"""伪装白名单进程源码模板（不参与应用导入，勿在业务代码中 import）。

用途
----
- 开发模式：:class:`~dlp_pass.core.writer_factory.DisguisedWriterFactory`
  复制本文件并用 PyInstaller 打包为 ``<进程名>.exe``（如 notepad.exe），
  运行时从 stdin 读取明文数据并落盘；
- 发布构建：``packaging/build_exe.py`` 预构建为 ``writer_stub.exe`` 并随
  主程序捆绑，运行时应目标进程名复制释放，无需目标机器安装 PyInstaller。

参数：``argv[1]`` = 输出文件路径，数据从 stdin 读取。
"""

import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        return 1
    out_file = Path(sys.argv[1])
    out_file.parent.mkdir(parents=True, exist_ok=True)
    raw_data = sys.stdin.buffer.read()
    out_file.write_bytes(raw_data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
