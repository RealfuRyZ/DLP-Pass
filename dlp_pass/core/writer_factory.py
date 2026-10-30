"""伪装白名单进程构建器。

原理（与原脚本一致）
------------------
将内存中的明文数据交给一个「伪装成白名单进程名」的 exe（如
``notepad.exe`` / ``pdfeditor.exe``）写盘，从而绕过 DLP 驱动对普通
进程的转加密 Hook，得到无密文标识的明文文件。

获取方式（按优先级）
------------------
1. 发布版（frozen）：主程序内置预构建的 ``writer_stub.exe`` 模板，
   运行时按目标进程名复制释放到缓存目录（无需安装 PyInstaller）；
2. 开发模式：以 ``packaging/writer_stub.py`` 为源码模板，用 PyInstaller
   （--onefile --noconsole）现场打包并缓存，构建参数与发布构建一致。
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from ..config import AppConfig
from .bundle import find_data_file, is_bundled

_logger = logging.getLogger(__name__)

_CREATE_NO_WINDOW = 0x08000000
# 伪装进程 stdin/stdout 协议版本：修改 packaging/writer_stub.py 的运行协议时
# 必须同步递增；旧缓存 exe 因缺少/不匹配版本标记而失效，自动重建或重新释放。
# v3（2.5.1）：批量模式新增读请求（字节数 = -1），用于委派白名单进程读取明文。
_PROTOCOL_VERSION = "3"
# 伪装进程源码模板属于构建输入，位于项目 packaging/ 目录（打包产物内置 exe 模板，运行时不再需要）
_STUB_SOURCE = Path(__file__).resolve().parents[2] / "packaging" / "writer_stub.py"


def _protocol_marker_path(exe_path: Path) -> Path:
    """缓存 exe 的协议版本标记文件路径（缓存有效性依据）。"""
    return exe_path.with_suffix(exe_path.suffix + ".protocol")


class DisguisedWriterFactory:
    """按需构建/释放并缓存伪装进程 exe（线程安全）。

    缓存有效性 = 文件存在且协议版本标记与当前代码一致：主程序升级后
    旧版本释放的缓存进程会被自动重建 / 重新释放，避免新旧协议不匹配。
    """

    def __init__(self, config: AppConfig):
        self._config = config
        self._cache_dir = Path(config.writer_cache_dir)
        self._lock = threading.Lock()

    def get_writer(self, proc_name: str) -> Path:
        """返回可直接执行的伪装进程 exe 路径，必要时构建/释放。"""
        clean_name = Path(proc_name).stem
        exe_path = self._cache_dir / f"{clean_name}.exe"
        with self._lock:
            if self._is_valid(exe_path):
                return exe_path
            bundled_stub = self._bundled_stub()
            if bundled_stub is not None:
                return self._release_bundled_stub(bundled_stub, exe_path)
            return self._build(clean_name)

    # ------------------------------------------------------------------ 内部

    @staticmethod
    def _bundled_stub() -> Path | None:
        """打包产物内置模板（PyInstaller --add-data / Nuitka --include-data-files）。"""
        return find_data_file("writer_stub.exe")

    def _release_bundled_stub(self, stub: Path, exe_path: Path) -> Path:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(stub, exe_path)
        _protocol_marker_path(exe_path).write_text(_PROTOCOL_VERSION, encoding="ascii")
        _logger.info("已从内置模板释放伪装进程: %s", exe_path)
        return exe_path

    @staticmethod
    def _is_valid(exe_path: Path) -> bool:
        try:
            if not exe_path.is_file() or exe_path.stat().st_size <= 0:
                return False
            version = _protocol_marker_path(exe_path).read_text(encoding="ascii")
            return version.strip() == _PROTOCOL_VERSION
        except OSError:
            return False

    def _build(self, clean_name: str) -> Path:
        if not _STUB_SOURCE.is_file():
            raise RuntimeError(f"缺少伪装进程源码模板: {_STUB_SOURCE}")
        python_exe = self._resolve_python()
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        _logger.info("正在构建伪装进程 %s.exe（首次使用需打包，请稍候）...", clean_name)
        with tempfile.TemporaryDirectory(prefix="dlp_pass_build_") as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            py_script = tmp_dir / f"{clean_name}.py"
            shutil.copy2(_STUB_SOURCE, py_script)

            cmd = [
                python_exe, "-m", "PyInstaller",
                "--onefile",
                "--noconsole",
                "--distpath", str(tmp_dir),
                "--workpath", str(tmp_dir / "build"),
                "--specpath", str(tmp_dir),
                str(py_script),
            ]
            subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                creationflags=_CREATE_NO_WINDOW,
            )

            built_exe = tmp_dir / f"{clean_name}.exe"
            if not built_exe.is_file():
                raise RuntimeError(f"PyInstaller 未生成 {clean_name}.exe")

            final_exe = self._cache_dir / f"{clean_name}.exe"
            shutil.move(str(built_exe), str(final_exe))
            _protocol_marker_path(final_exe).write_text(_PROTOCOL_VERSION, encoding="ascii")
            _logger.info("伪装进程已构建并缓存: %s", final_exe)
            return final_exe

    def _resolve_python(self) -> str:
        """定位用于打包的 Python 解释器（仅开发模式使用）。"""
        candidates: list[str] = []
        if self._config.python_executable:
            candidates.append(self._config.python_executable)
        if not is_bundled():
            candidates.append(sys.executable)
        for name in ("python", "python3"):
            found = shutil.which(name)
            if found:
                candidates.append(found)
        if is_bundled() and "python" in Path(sys.executable).name.lower():
            candidates.append(sys.executable)
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                return candidate
        raise RuntimeError("未找到可用于构建伪装进程的 Python 解释器")
