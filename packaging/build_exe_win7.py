"""构建 Windows 7 兼容版目录程序（Python 3.8 + PyQt5，64 位）。

背景
----
现代版（``build_exe.py``）依赖 PyQt6，而 Python 3.9+ 与 Qt6 均不支持
Windows 7。本脚本使用最后一个支持 Win7 的 CPython（3.8）与 Qt 5.15
（PyQt5）构建兼容版；界面代码通过 ``dlp_pass.ui_qt.qt_compat`` 兼容层
与 PyQt6 完全共享，两版行为一致。流水线同 ``build_exe.py``
（标准 PyInstaller 目录版打包，无代码加密/混淆）。

用法（必须使用 Python 3.8 虚拟环境执行）::

    .venv38\\Scripts\\python.exe packaging\\build_exe_win7.py

依赖安装（一次性）::

    E:\\Python38\\python.exe -m venv .venv38
    .venv38\\Scripts\\python.exe -m pip install -r requirements-win7.txt

产物：

- 目录版：``packaging/dist/DLP-Pass-Win7/DLP-Pass-Win7.exe``
- 绿色版：``packaging/dist/DLP-Pass-Win7-Portable.exe``

（Windows 7 SP1 ~ Windows 11 均可运行）
"""

from __future__ import annotations

import sys
from pathlib import Path

if sys.version_info[:2] != (3, 8):
    sys.exit(
        "Win7 兼容版必须使用 Python 3.8 构建（推荐 .venv38 虚拟环境）；\n"
        f"当前解释器：{sys.version}\n"
        "用法：.venv38\\Scripts\\python.exe packaging\\build_exe_win7.py"
    )

PACKAGING_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PACKAGING_DIR))

import build_exe as base  # noqa: E402  （复用同一条构建流水线）

# 覆盖为 Win7 兼容版参数（独立构建目录与产物名）
base.APP_NAME = "DLP-Pass-Win7"
base.BUILD_DIR = PACKAGING_DIR / "build" / "win7"


def main() -> None:
    print("构建目标：Windows 7 SP1+ 兼容版（Python 3.8 + PyQt5 + PyInstaller 5.13.2）")
    base.main()
    if "--portable" in sys.argv[1:]:
        exe = base.DIST_DIR / f"{base.APP_NAME}-Portable.exe"
    else:
        exe = base.DIST_DIR / base.APP_NAME / f"{base.APP_NAME}.exe"
    if exe.is_file():
        print("\n提示：Win7 兼容版也可在 Windows 10/11 上运行；")
        print("      现代版（DLP-Pass.exe，PyQt6）仅支持 Windows 10/11。")


if __name__ == "__main__":
    main()
