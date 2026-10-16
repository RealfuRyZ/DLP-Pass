# 更新日志（Changelog）

本文件记录 DLP-Pass 的版本变更。版本号遵循语义化版本，映射数据版本另见 `mappings/`。
每个版本的说明会由 CI（`.github/workflows/release.yml`）自动摘录到对应的 GitHub Release 正文。

## 2.3.2 (2026-10-16)

### 修复

- **单选框选中态样式**：修正「输出方式」单选框选中时渲染成圆角方形的问题（选中态 4px
  加粗边框改变了指示器盒尺寸，导致圆角比例失真）；选中与未选中保持相同的圆形几何，
  仅通过颜色切换区分，与「自动跳过未加密文件」复选框的选中风格一致。

### 优化

- **批量解密进程复用**：后台按文件后缀分组排序，同后缀文件连续处理时伪装进程只启动
  一次并在组内复用（`writer_stub.py` 新增常驻批处理协议），后缀切换 / 组内处理完成 /
  任务取消时统一关闭，避免逐文件反复创建、销毁模拟进程；前台列表顺序与统计行为不变。
- 伪装进程缓存增加协议版本标记：主程序升级后旧缓存 exe 自动失效，重建 / 重新释放。

## 2.3.1 (2026-10-13)

### 新增功能

- **解密输出方式切换**：界面「输出方式与目录」卡片新增单选切换，可选
  **原地解密覆盖**（解密结果直接覆盖原始文件路径）或 **输出到指定目录**
  （写入所选目录，原始文件保持不变，保留原有相对目录结构）。
  - 选择「输出到指定目录」时，若输出目录未填写或路径无效（非法字符、非绝对路径、
    指向文件而非文件夹），点击「开始恢复 / 导出」会给出校验提示并阻止执行；
  - 选择「原地解密覆盖」时，输出目录输入框与「浏览…」按钮即时置灰禁用，
    切换无需刷新界面；开始批量任务前增加一次覆盖确认。
- **原地覆盖的安全保障**：核心解密流程改为「先校验、后覆盖」——伪装进程先把明文写入
  同目录临时文件，确认其已不含密文标识后才重命名替换原文件；校验不通过时原文件
  完整保留并继续尝试下一个备用进程。
- **进程映射扩充** `mappings/20261016.1.json`：在 `20261009.1` 全部条目原样保留的基础上新增
  113 条映射（共 153 条），覆盖工程类（AutoCAD / SolidWorks / CATIA / Creo·Pro-E / NX·UG /
  Inventor / MicroStation / Revit / ArchiCAD / Navisworks / ANSYS / ABAQUS / COMSOL /
  HyperMesh / LS-DYNA / Nastran·Patran / Altium Designer / Cadence Virtuoso / Siemens EDA·PADS /
  华大九天 Empyrean / Teamcenter / Windchill / Aras Innovator 等）与图像设计类
  （Photoshop / Illustrator / InDesign / Lightroom / Figma / Sketch / CorelDRAW /
  Affinity Designer·Photo·Publisher / Blender / Substance 3D / 3ds Max / Maya / ZBrush /
  Premiere Pro / After Effects / DaVinci Resolve / Pixso / MasterGo / 即时设计 /
  创客贴 / 稿定设计 等）常见后缀 → 白名单伪装进程队列。程序启动时自动读取最新版本映射。

### 变更

- 左侧卡片标题由「输出目录」改为「输出方式与目录」。
- 发布流程：Release 正文由自动生成的 `Full Changelog` 链接扩展为
  「本版本更新说明 + Full Changelog 链接」，内容取自本文件对应版本段落。

## 2.3.0 (2026-10-12)

### 新增功能

- 开源重构（GPLv3）：移除 MariaDB 集中映射与解密审计，程序完全离线、无任何网络行为。
- 版本化本地映射数据 `mappings/`：每个 JSON 文件即一个版本，启动时自动读取版本号最大者。
- Qt 深色主题界面：拖拽批量解密、实时日志、状态徽标与统计卡片，PyQt6 / PyQt5 双绑定同一套代码。
- CI 自动发布：推送 `v*` 标签即在 GitHub Actions 上构建现代版与 Win7 兼容版并创建 Release，
  附 `SHA256SUMS.txt`。
