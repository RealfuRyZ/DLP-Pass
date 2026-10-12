"""构建单文件可执行程序（现代版：PyQt6，Windows 10/11）。

流程（标准 PyInstaller，无代码加密/混淆——源码即发布内容，可审计）：

1. 预构建伪装进程模板 ``writer_stub.exe``（独立 onefile，运行时按进程名复制释放）；
2. PyInstaller 单文件打包 ``main.py``：
   - 捆绑 ``mappings/`` 映射数据目录（exe 同目录放置同名目录可覆盖内置数据）；
   - 捆绑 ``writer_stub.exe``；
3. 输出产物路径 / 大小 / SHA256。

用法（项目主虚拟环境中执行）::

    .venv\\Scripts\\python.exe packaging\\build_exe.py

产物：``packaging/dist/DLP-Pass.exe``

Win7 兼容版请使用 ``build_exe_win7.py``（Python 3.8 + PyQt5，见该脚本说明）。
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PACKAGING_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import dlp_pass  # noqa: E402  （读取版本号）

BUILD_DIR = PACKAGING_DIR / "build" / "modern"
DIST_DIR = PACKAGING_DIR / "dist"
APP_NAME = "DLP-Pass"
PYTHON = sys.executable


def step(title: str) -> None:
    print(f"\n===== {title} =====", flush=True)


def run(cmd: list[Any]) -> None:
    parts = [str(part) for part in cmd]
    print("$", " ".join(parts), flush=True)
    subprocess.run(parts, check=True, cwd=str(ROOT))


def build_writer_stub() -> Path:
    """预构建伪装进程模板（打包后由主程序按目标进程名复制释放）。"""
    step("1/2 预构建伪装进程模板 writer_stub.exe")
    out_dir = BUILD_DIR / "writer_dist"
    run([
        PYTHON, "-m", "PyInstaller",
        "--onefile", "--noconsole", "--clean", "--noconfirm",
        "--distpath", out_dir,
        "--workpath", BUILD_DIR / "writer_work",
        "--specpath", BUILD_DIR / "writer_work",
        ROOT / "dlp_pass" / "core" / "writer_stub.py",
    ])
    stub = out_dir / "writer_stub.exe"
    if not stub.is_file():
        sys.exit("writer_stub.exe 构建失败")
    print(f"writer_stub.exe: {stub.stat().st_size / 1024:.0f} KB")
    return stub


def build_app(stub: Path) -> Path:
    """PyInstaller 单文件打包：入口 main.py + 随包数据（mappings/、writer_stub.exe）。"""
    step("2/2 PyInstaller 打包单文件程序")
    args: list = [
        PYTHON, "-m", "PyInstaller",
        "--onefile", "--noconsole", "--clean", "--noconfirm",
        "--name", APP_NAME,
        "--distpath", DIST_DIR,
        "--workpath", BUILD_DIR / "app_work",
        "--specpath", BUILD_DIR / "app_work",
    ]
    mappings_dir = ROOT / "mappings"
    if mappings_dir.is_dir():
        args += ["--add-data", f"{mappings_dir};mappings"]
    else:
        print("警告：项目根目录缺少 mappings/，发布版将只依赖内置兜底规则")
    args += [
        "--add-data", f"{stub};.",
        "--exclude-module", "tkinter",
        ROOT / "main.py",
    ]
    run(args)

    exe = DIST_DIR / f"{APP_NAME}.exe"
    if not exe.is_file():
        sys.exit("单文件程序构建失败")
    return exe


def main() -> None:
    started = time.time()
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR, ignore_errors=True)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    stub = build_writer_stub()
    exe = build_app(stub)

    digest = hashlib.sha256(exe.read_bytes()).hexdigest()
    print("\n===== 构建完成 =====")
    print(f"产物   : {exe}")
    print(f"大小   : {exe.stat().st_size / 1024 / 1024:.1f} MB")
    print(f"SHA256 : {digest}")
    print(f"版本   : {dlp_pass.__version__}")
    print(f"耗时   : {time.time() - started:.0f} 秒")
    print("\n部署验证（可选）:")
    print(f'  "{exe}" --selftest <待解密的加密文件>')


if __name__ == "__main__":
    main()
