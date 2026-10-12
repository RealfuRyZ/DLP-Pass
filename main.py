"""DLP-Pass 程序入口：启动图形界面。

项目结构：
- ``dlp_pass/core`` ：解密业务逻辑（读取明文内存流 -> 伪装白名单进程落盘 -> 密文校验），
  与界面完全解耦，可独立复用或在命令行 / 测试中调用；
- ``dlp_pass/ui_qt``：Qt 深色主题界面（PyQt6/PyQt5 双兼容），
  采用 QThread 异步架构，支持拖拽文件 / 文件夹批量解密；
- ``mappings/``     ：「文件后缀 -> 伪装进程」版本化映射数据；程序启动时
  始终自动读取版本号最新的文件（完全离线，无数据库依赖）。

源码运行::

    python main.py
"""

from dlp_pass.ui_qt.app import run

if __name__ == "__main__":
    run()
