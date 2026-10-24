"""DLP-Pass 程序入口：图形界面 / 命令行解密 / 自检。

项目结构：
- ``dlp_pass/core`` ：解密业务逻辑（读取明文内存流 -> 伪装白名单进程落盘 -> 密文校验），
  与界面完全解耦，可独立复用或在命令行 / 测试中调用；
- ``dlp_pass/ui_qt``：Qt 深色主题界面（PyQt6/PyQt5 双兼容），
  采用 QThread 异步架构，支持拖拽文件 / 文件夹批量解密；
- ``dlp_pass/cli.py``：无界面命令行（``--decrypt`` / ``--selftest``），供安装包的
  资源管理器右键菜单调用；
- ``mappings/``     ：「文件后缀 -> 伪装进程」版本化映射数据；程序启动时
  始终自动读取版本号最新的文件（完全离线，无数据库依赖）。

源码运行::

    python main.py
    python main.py --decrypt --in-place "D:\\资料"
    python main.py --selftest <加密文件>
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    """按参数选择图形界面或无界面命令行。"""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if any(
        argument in ("--decrypt", "--selftest", "--progress", "-h", "--help")
        for argument in arguments
    ):
        from dlp_pass.cli import main as cli_main

        return cli_main(arguments)

    from dlp_pass.ui_qt.app import run

    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
