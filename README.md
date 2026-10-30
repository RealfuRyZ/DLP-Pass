# DLP-Pass

> **⚠️ 用途声明（请务必阅读）**
>
> 本工具面向**自有或已获授权的企业 DLP（数据防泄漏）透明加密环境**，用于将已加密文件
> 批量恢复为明文（如：离职交接、资料归档、系统迁移、日常取数）。
> 请仅在**你有权处置这些文件**的场景下使用，并遵守所在组织的保密制度与当地法律法规。
> **严禁用于任何未经授权的场景。**

DLP-Pass 是一个**核心解密完全离线**的 Windows 桌面工具：把被 DLP 客户端透明加密的文件批量恢复为明文。
拖入文件 / 文件夹即可——自动识别加密状态、匹配导出进程、校验导出结果。

本仓库为开源实现（GPLv3）：**源码即发布内容，不含任何代码加密或混淆**；
程序不连接任何数据库；唯一的可选网络请求是启动时向 GitHub Release 查询最新版本（可在
`config.json` 中用 `check_updates: false` 关闭，失败时静默降级）。你可以审计每一行代码，
也可以自行构建发布物。

## 界面预览

![界面截图](./assets/screenshot.png)

## 下载预构建版本

每个版本标签（tag）由 GitHub Actions 自动构建并发布到本仓库
[Releases](https://github.com/RealfuRyZ/DLP-Pass/releases)，附 `SHA256SUMS.txt`；
构建日志公开，可核对源码与产物的一致性：

| 文件 | 适用系统 | 技术栈 |
| --- | --- | --- |
| `DLP-Pass-Setup-<版本>.exe` | Windows 10 / 11（64 位） | Python 3.14 + PyQt6 |
| `DLP-Pass-Setup-<版本>-Win7.exe` | Windows 7 SP1 ~ 11（64 位） | Python 3.8 + PyQt5 |
| `DLP-Pass-Portable.exe` | Windows 10 / 11（64 位） | 单文件绿色版 |
| `DLP-Pass-Win7-Portable.exe` | Windows 7 SP1 ~ 11（64 位） | 单文件绿色版 |

安装包支持覆盖升级并可选注册资源管理器右键菜单；绿色版解压后直接运行，无需安装。

> 首次运行如遇 Windows SmartScreen 提示（免费开源软件未做商业签名的常见现象），
> 选择「更多信息 → 仍要运行」即可；你也可以按下文自行构建，产物完全等价。

## 工作原理

企业 DLP 客户端对文档做**透明加密**：受信任进程读取文件时由驱动自动解密，
不受信任进程（包括普通脚本 / 打包程序）读到的则是带 `%TSD-Header-###%`
标识的密文容器。DLP-Pass 的恢复流程：

1. **读取明文**：优先用当前 Python 进程直接读取（DLP 白名单内时驱动自动解密）；
   读到原始密文时，按映射表以**伪装白名单进程**（如 `WINWORD` / `EXCEL`）读取明文，
   `cmd.exe` / `python` 作为兜底——艾特盾等只对客户端显式授权进程解密的厂商，
   cmd 读回的是与磁盘一致的密文，伪装进程读取是唯一可靠途径（issue #1）；
2. **匹配伪装进程**：按 `mappings/` 映射表把文件后缀匹配到一组 DLP 白名单进程名
   （如 `.docx -> winword, wps, kwps`）；
3. **伪装落盘**：释放对应进程名的模板 exe，将明文经 stdin 写入 `*.pass` 临时文件，
   再重命名为目标文件——驱动按白名单进程放行，落盘即为明文；
4. **密文校验**：用 PowerShell（非白名单视角）检查输出文件头，确认不含密文标识，
   失败则依次尝试备用进程。

> 前提：源文件与程序运行在**同一套 DLP 策略环境**内；换机器/换环境无法解密属正常现象，
> 请联系你的 DLP 管理员。

## 功能特性

- 拖拽文件 / 文件夹批量解密，保留原有目录结构，自动跳过未加密文件
- **两种解密输出方式**：「输出到指定目录」（原始文件保持不变）或「原地解密覆盖」
  （解密结果直接替换原文件；先校验为明文再覆盖，校验失败时原文件完整保留）
- 现代化亮/暗双主题界面：任务列表、实时日志、进度条、状态徽标与统计卡片（PyQt6 / PyQt5
  双绑定同一套代码）；顶栏映射规则版本号左侧可一键切换配色方案，偏好自动持久化
- **调试模式**：顶栏主题按钮左侧新增可勾选的「调试模式」开关，开启后解密流程每个关键步骤的详细日志写入 logs/dlp_pass_debug.log，尝试解密过程中的所有中间文件（磁盘原始字节、委派读取结果、临时落盘文件、映射文件副本等）留档到 logs/debug/<时间戳>/，便于远程排查「为什么解不开」；首次开启会弹出磁盘占用提醒，开关状态自动持久化
- 自动密文检测与导出结果校验（确保最终输出为明文）
- **伪装白名单进程读取（v2.5.1）**：读到磁盘密文时，先按映射表以授权进程名（WINWORD / EXCEL 等）复制释放的读取 stub 委派读取明文，cmd / python 降为兜底；解决艾特盾等「仅对客户端显式授权进程解密」厂商 cmd 读回仍是密文的问题（issue #1），并自动识别 WindowsApps 商店 Python 存根（9009）等不可用兜底途径
- 版本化本地映射数据（`mappings/` 目录，启动时始终读取最新版本，改映射无需重装）
- 目录版安装包（`--onedir`）：内置全部运行环境，安装后启动快，可在系统设置中卸载
- 资源管理器右键菜单：选中文件 / 文件夹即可原地解密（多选自动合并为一批），
  独立的进度窗口可随时取消，完成后弹出结果提示，无需打开主界面
- 统一程序 Logo：标题栏、任务栏、窗口顶部与安装包图标均使用 `assets/logo.*`
- 启动时检查 GitHub Release：已是最新时版本号绿色显示；有新版本时版本号红色并显示
  「有新版本可用」，点击直达 Release 页面；鼠标悬浮版本号显示检查结果
  （「当前为最新版本」/「有新版本可用 vX.X.X」）；网络失败 / 超时则仅显示当前版本
  （版本源为 GitHub `releases/latest` API，超时 4 秒，可通过 `check_updates: false` 关闭）

## 项目结构

```text
DLP-Pass/
├── main.py                  # 程序入口（界面 / 命令行分发）
├── dlp_pass/
│   ├── config.py            # 应用配置与内置兜底映射规则
│   ├── cli.py               # 无界面命令行（--decrypt / --selftest，右键菜单调用）
│   ├── logging_setup.py     # 图形界面与命令行共用的日志初始化
│   ├── debug_mode.py        # 调试模式：详细日志开关 + 中间过程文件留档
│   ├── core/                # 解密业务逻辑（与界面完全解耦，可独立复用）
│   │   ├── mapping_loader.py    # mappings/ 版本化映射加载（取最新版）
│   │   ├── output_mode.py       # 输出模式：原地覆盖 / 指定目录（目录校验 + 任务编排）
│   │   ├── decrypt_service.py   # 解密主流程：读取 -> 伪装落盘 -> 校验（含原地覆盖保护）
│   │   ├── source_reader.py     # 明文读取（含委派白名单进程）
│   │   ├── verifier.py          # 密文检测（PowerShell 非白名单视角）
│   │   ├── writer_factory.py    # 伪装进程模板释放与构建
│   │   └── file_collector.py bundle.py models.py  # 遍历/资源定位/数据结构
│   └── ui_qt/               # Qt 界面（暗色 / 亮色双主题，settings.py 持久化偏好）
│       ├── qt_compat.py         # PyQt6/PyQt5 绑定兼容层
│       ├── app.py               # 应用装配与日志
│       ├── main_window.py       # 主窗口与交互流程
│       ├── progress.py          # 右键菜单独立进度窗口（不打开主界面）
│       └── widgets.py theme.py workers.py  # 控件 / 主题 / QThread 后台任务
├── mappings/                # ★ 映射数据：每文件一个版本（见目录内 README）
├── packaging/               # 构建脚本与打包输入
│   ├── build_exe.py             # 现代版目录 / 绿色版构建流水线
│   ├── build_exe_win7.py        # Win7 兼容版目录 / 绿色版构建流水线
│   ├── installer.nsi            # NSIS 安装包脚本（右键菜单 / 安装 / 卸载）
│   ├── build_installer.ps1      # 读取版本号并调用 makensis 的构建脚本
│   └── writer_stub.py           # 伪装进程源码模板（构建输入，不参与运行时导入）
├── .github/workflows/     # CI：打 tag 自动构建并发布 Release（更新说明取自 CHANGELOG.md）
├── tests/                 # 单元测试（标准库 unittest，无第三方依赖）
├── assets/                # Logo（svg/png/ico）与文档图片
├── CHANGELOG.md           # 版本更新说明（CI 摘录为 Release 正文）
├── requirements.txt       # 现代版：运行 + 构建依赖（Python 3.9+）
├── requirements-win7.txt  # Win7 兼容版构建依赖（Python 3.8）
└── LICENSE                # GPLv3
```

## 映射数据（mappings/）

「文件后缀 -> 白名单伪装进程」的对应关系存放于 [`mappings/`](./mappings/README.md)：

- 每个 JSON 文件即一个版本，文件名即版本号（如 `20261009.1.json`）；
- **程序启动时始终读取版本号最大的文件**作为当前生效映射（无需重装/重新构建）；
- 目录查找优先级：`config.json` 的 `mappings_dir` -> exe 同目录 `mappings/` ->
  随包内置 `mappings/` -> 项目根 `mappings/`；
- 找不到任何映射时使用内置兜底规则（`.pdf -> pdfeditor`，其余 -> `notepad, winword, wps`）。

## 配置（可选）

程序**零配置即可运行**。如需调整，在 exe 同目录（或项目根）放置 `config.json`：

```json
{
    "powershell_timeout": 10.0,
    "default_output_dir": "D:/解密输出",
    "mappings_dir": "D:/my-mappings",
    "check_updates": false
}
```

| 字段 | 说明 | 默认值 |
| --- | --- | --- |
| `encryption_marker` | 密文容器标识串 | `%TSD-Header-###%` |
| `powershell_timeout` | 密文校验超时（秒） | `10.0` |
| `default_output_dir` | 界面初始输出目录 | 桌面/`DLP解密输出` |
| `mappings_dir` | 显式指定映射目录 | 空（自动查找） |
| `writer_cache_dir` | 伪装进程模板缓存目录 | `%TEMP%/dlp_pass_writers` |
| `python_executable` | 明文读取委派的 python 路径 | 空（自动探测） |
| `check_updates` | 启动时检查 GitHub Release 最新版本 | `true` |

> 旧版基于 MariaDB 的集中映射与解密审计已在开源重构（v2.3.0）中**整体移除**。
> 除可关闭的版本检查外，程序不连接任何数据库或网络服务。

## 快速开始（源码运行）

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py            :: 启动界面
python main.py --selftest <加密文件>   :: 无界面自检（退出码 0=成功）
```

## 命令行与资源管理器右键菜单

除图形界面外，exe 还提供无界面命令行（发布版为 `--noconsole`，过程与结果写入
`<安装目录>\logs\dlp_pass.log`；源码运行时写入 `<项目根目录>\logs\dlp_pass.log`；程序
目录不可写时回退到 `%LOCALAPPDATA%\DLP-Pass\logs\dlp_pass.log`）：

```bat
:: 原地解密覆盖（独立进度窗口；先校验为明文再替换，失败时原文件保持不变）
DLP-Pass.exe --decrypt --in-place --progress --notify "D:\资料\a.docx" "D:\资料\b.pdf"

:: 输出到指定目录（保留文件夹的相对目录结构）
DLP-Pass.exe --decrypt --output-dir "D:\解密输出" "D:\资料"

:: 不指定 --in-place / --output-dir 时，输出到 config.json 的 default_output_dir
DLP-Pass.exe --decrypt "D:\资料"
```

常用参数：`--include-plain` 连未检测到加密标识的文件也导出；`--progress` 打开独立的
进度窗口（不打开主界面，可取消）；`--notify` 结束后弹结果框；`--confirm` 开始前弹确认框；
`--shell-batch` 汇总资源管理器多选产生的多个进程。选项与路径可以混排。退出码：`0` 全部
成功（跳过未加密文件不算失败）、`1` 存在失败 / 路径缺失 / 输出冲突、`2` 参数或输出目录无效。

安装包注册的右键菜单命令为：

```text
"<安装目录>\DLP-Pass.exe" --in-place --progress --notify --shell-batch --decrypt "%1"
```

Windows 对多选文件通常会逐项启动菜单命令；`--shell-batch` 通过本机命名管道把同一批次的
路径汇总到首个进程，只执行一次解密、只显示一个进度窗口。扫描阶段显示不确定进度，解密
阶段显示 `已完成 / 总数`；点击「取消」会在当前文件处理完成后停止。结束后弹出成功 / 失败
汇总提示，不会打开主界面。右键菜单注册在 `HKCU\Software\Classes` 下，覆盖**文件**、
**文件夹**和**文件夹空白处**三种上下文；Windows 11 中该菜单位于「显示更多选项」里。

## 构建发布

### CI 自动构建（推荐）

发布新版本只需打标签：

```bat
git tag v2.4.1
git push origin v2.4.1
```

GitHub Actions（`.github/workflows/release.yml`）会自动：在官方 Windows runner 上
分别构建现代版与 Win7 兼容版（各跑一遍单元测试 + `--selftest` 冒烟），生成 onedir
程序、NSIS 安装包和单文件绿色版，然后创建同名 Release，附上安装包、绿色版与
`SHA256SUMS.txt`。

Release 正文（更新说明）取自 [`CHANGELOG.md`](./CHANGELOG.md) 中对应版本的段落，
并保留 `**Full Changelog**` 链接，因此发布前先在该文件补写本版本说明即可。

### 本地构建

```bat
:: 现代版（Windows 10/11，onedir）—— 主虚拟环境
.venv\Scripts\python.exe packaging\build_exe.py
:: 产物：packaging\dist\DLP-Pass\DLP-Pass.exe

:: 现代版单文件绿色版
.venv\Scripts\python.exe packaging\build_exe.py --portable
:: 产物：packaging\dist\DLP-Pass-Portable.exe

:: Win7 兼容版（Windows 7 SP1+，onedir）—— 需要 Python 3.8 专用虚拟环境
E:\Python38\python.exe -m venv .venv38
.venv38\Scripts\python.exe -m pip install -r requirements-win7.txt
.venv38\Scripts\python.exe packaging\build_exe_win7.py
:: 产物：packaging\dist\DLP-Pass-Win7\DLP-Pass-Win7.exe

:: Win7 兼容版单文件绿色版
.venv38\Scripts\python.exe packaging\build_exe_win7.py --portable
:: 产物：packaging\dist\DLP-Pass-Win7-Portable.exe
```

两条流水线默认使用标准 PyInstaller 目录版（`--onedir`）打包；加 `--portable` 可生成
单文件绿色版，两者均**不做任何代码加密/混淆**。构建输出包含产物目录 / 文件与 SHA256，
可直接用于发布说明。目录版启动时无需把整个运行环境解压到 `%TEMP%`，也适合右键菜单
这种短生命周期调用；绿色版复制即可运行，但首次启动需要解压。

### 制作 NSIS 安装包

前置条件：已安装 **NSIS 3**（`makensis.exe` 位于 PATH 或常见安装目录），并已生成
`packaging\dist\DLP-Pass\DLP-Pass.exe`（或
`packaging\dist\DLP-Pass-Win7\DLP-Pass-Win7.exe`）。

```bat
:: 现代版安装包（默认使用 dist\DLP-Pass 目录）
powershell -ExecutionPolicy Bypass -File packaging\build_installer.ps1

:: Win7 兼容版安装包（使用 dist\DLP-Pass-Win7 目录）
powershell -ExecutionPolicy Bypass -File packaging\build_installer.ps1 -AppDir DLP-Pass-Win7 -AppExe DLP-Pass-Win7.exe
```

脚本会从 `dlp_pass\__init__.py` 读取版本号，输出
`packaging\dist\DLP-Pass-Setup-<版本>.exe`。也可以手动编译：

```bat
cd packaging
"D:\Program Files (x86)\NSIS\makensis.exe" /DAPP_VERSION=2.4.1 /DAPP_DIR=DLP-Pass installer.nsi
```

安装包默认安装到 **`C:\Program Files\DLP-Pass`**（需要管理员权限，目录页可修改），
递归复制 onedir 产物、`mappings/`、README / LICENSE / CHANGELOG，创建桌面和开始菜单
快捷方式及 `logs/` 目录，并在组件页提供「资源管理器右键菜单」选项。首次安装时若检测到
旧版本（无论安装在 C:\、D:\ 或旧的 per-user 目录），安装程序会自动复用原安装目录进行
覆盖升级，保留 `config.json`、`logs` 等用户数据，只替换程序文件；也可在目录页手动改选
路径。右键菜单、快捷方式和卸载信息写入当前用户 HKCU；`logs/` 会单独授予 Users 修改
权限，保证非提升权限的右键解密也能写日志，授权失败时程序回退到
`%LOCALAPPDATA%`。Win7 版安装后会把 `DLP-Pass-Win7.exe` 重命名为统一的
`DLP-Pass.exe`。卸载会删除右键菜单、桌面 / 开始菜单快捷方式和整个安装目录。

验证安装与右键菜单：

```powershell
# 1. 检查安装目录、桌面快捷方式、日志目录和右键菜单命令
$install = (Get-ItemProperty -LiteralPath 'HKCU:\Software\DLP-Pass').InstallDir
Get-ChildItem -LiteralPath $install
Get-Item -LiteralPath (Join-Path ([Environment]::GetFolderPath('Desktop')) 'DLP-Pass.lnk')
Get-ChildItem -LiteralPath (Join-Path $install 'logs')
Get-ItemProperty -LiteralPath 'HKCU:\Software\Classes\*\shell\DLPPass.Decrypt\command'
Get-ItemProperty -LiteralPath 'HKCU:\Software\Classes\Directory\shell\DLPPass.Decrypt\command'

# 2. 检查卸载信息
Get-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\DLP-Pass'

# 3. 静默安装 / 卸载（测试用；/D= 必须是最后一个参数）
& "$env:TEMP\DLP-Pass-Setup-2.4.1.exe" /S "/D=$env:TEMP\DLP-Pass-Test"
& "$env:TEMP\DLP-Pass-Test\Uninstall.exe" /S
```

安装后右键选中一个或多个加密文件（或文件夹），选择「使用 DLP-Pass 解密」即可。
若菜单未立即出现，重启资源管理器或在任务管理器中重启 `explorer.exe`；
失败详情见 `<安装目录>\logs\dlp_pass.log`。

## 开发约定

- **双解释器兼容**：核心与界面代码必须同时在 Python 3.8（Win7 构建链）与最新
  Python（开发环境）下可运行；文件顶部保留 `from __future__ import annotations`，
  避免使用 3.9+ 才有的语法/标准库 API。
- **Qt 绑定兼容**：界面只从 `ui_qt.qt_compat` 导入 Qt 名字；PyQt6 与 PyQt5 5.15
  均支持作用域枚举写法（如 `Qt.AlignmentFlag.AlignCenter`），无需分叉代码。
- **core 与界面解耦**：`dlp_pass.core` 不导入任何 Qt/界面代码，可独立测试与复用。
- 中文文档字符串 + 完整类型注解；公开 API 的行为约定写入 docstring。
- 运行测试：`python -m unittest discover -s tests -v`（两个虚拟环境都应通过）。

## 隐私与安全

- 核心解密完全离线：无数据库；启动时的 GitHub Release 版本检查仅发送
  `User-Agent: DLP-Pass/<版本>`，不发送文件、路径或内容，可用 `check_updates: false` 关闭；
- 代码明文打包进发布物（标准 PyInstaller），可反编译审计——这正是开源的意义；
- 日志与解密过程只写入本机（安装版为 `<安装目录>\logs`，源码运行为 `<项目根目录>\logs`；
  程序目录不可写时回退到 `%LOCALAPPDATA%\DLP-Pass\logs`）。

## 常见问题

- **Windows 7 提示 `python314.dll` / `LoadLibrary 找不到指定的模块`** —— 那是现代版
  （Python 3.14/Qt6 要求 Win10+）；Win7 请安装 `DLP-Pass-Setup-<版本>-Win7.exe`。
- **顶部徽标显示「映射规则 内置兜底」** —— `mappings/` 目录缺失或内容不可用，
  核心解密仍可用（兜底规则），按目录内 README 补充即可。
- **导出后校验仍为密文** —— 当前 DLP 策略未信任所使用的伪装进程名，
  请换用/增补白名单进程映射（见 `mappings/README.md`）。
- **遇到无法解密的文件 / DLP 工具兼容性** —— 目前仅测试了少量 DLP 工具；若遇到无法
  解密的文件，请通过 [GitHub Issue](https://github.com/RealfuRyZ/DLP-Pass/issues) 提交
  反馈，并尽量附上 DLP 客户端名称与版本、文件后缀、错误信息及相关日志段落。
- **首次启动稍慢** —— 安装版无需解压运行环境；单文件绿色版首次运行需解压，且可能被
  杀毒软件扫描，稍慢属正常。

## 许可

本项目以 **GNU GPLv3** 许可发布（见 [LICENSE](./LICENSE)）。
注意：PyQt6/PyQt5 本身为 GPL 许可，分发含 PyQt 的二进制需遵守 GPL 条款。
