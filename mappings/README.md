# mappings/ 映射规则目录

本目录存放「文件后缀 → 白名单伪装进程」映射规则。
程序**启动时始终读取版本号最大的一个 JSON 文件**作为当前生效映射
（解析逻辑见 `dlp_pass/core/mapping_loader.py`）。

## 版本文件命名

- 每个文件即一个版本：`<版本号>.json`，例如 `20261009.1.json`；
- 版本号推荐使用 `日期.序号` 格式（`YYYYMMDD.N`）；
- 排序规则：版本号按 `.` / `_` / `-` 分段，数字段按数值比较，取最大者生效；
- 以 `_` 或 `.` 开头的文件（如 `_draft.json`）视为草稿，不会生效。

## 文件格式

```json
{
  "version": "20261009.1",
  "updated": "2026-10-09",
  "description": "版本说明（可选）",
  "rules": [
    {
      "extension": ".pdf",
      "primary": "pdfeditor",
      "alternatives": ["AcroRd32", "FoxitPDF", "FoxitPDFEditor"],
      "priority": 100,
      "enabled": true
    }
  ]
}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `extension` | 是 | 文件后缀（小写、含点），如 `.pdf` |
| `primary` | 是 | 首选伪装进程名（**不含** `.exe`；须为目标机器 DLP 客户端的白名单进程） |
| `alternatives` | 否 | 备用进程列表；主进程失败或导出仍为密文时依次尝试 |
| `priority` | 否 | 同一后缀出现多条规则时取数值最大者（默认 `0`） |
| `enabled` | 否 | `false` 的规则被忽略（默认 `true`） |

`version` / `updated` / `description` 仅作说明；**生效版本以文件名为准**。

## 维护流程

1. 复制目录内当前最大版本文件，改名为新版本号（如 `20261012.1.json`）；
2. 修改其中的 `rules`；
3. 无需重新构建程序：下次启动自动读取最大版本。

如需在不改包的情况下覆盖内置映射：把整个 `mappings/` 目录放到 **exe 同目录**，
其优先级高于随包内置版本；也可在 `config.json` 中用 `mappings_dir` 显式指定。

## 兜底行为

目录缺失、无有效文件或某后缀未配置时，使用内置兜底规则
（见 `dlp_pass/config.py`）：

- `.pdf` -> `pdfeditor`，备用 `AcroRd32`、`FoxitPDFEditor`
- 其它后缀 -> `notepad`，备用 `winword`、`wps`
