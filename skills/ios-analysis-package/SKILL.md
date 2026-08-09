---
name: ios-analysis-package
description: >-
  将 iPhone 真机日志 / Instruments trace 的分析结果整理为 Markdown，并把 Markdown、过滤日志、原始日志片段、
  对应 trace/toc/hangs 文件打包为 zip，方便用户保存、归档或发给研发同学。
---

# 分析结果归档打包

当用户要求“打包分析结果”“保存这次分析”“把 markdown、日志、trace 打包”“生成可分享压缩包”时使用本 Skill。

本 Skill 不负责重新采集日志或录制 Instruments；采集请使用 `ios-device-log`，录现场与 stop 分析请使用 `ios-device-profile`。

## 输出位置

默认将压缩包放到：

```bash
~/Desktop/TraceCite/analysis/
```

压缩包命名建议：

```text
export_{YYYYMMDDTHHMM}.zip
```

`--tag` 写入 manifest，用中性问题类型，例如 `demo-sync-timeout`、`profile-leak`、
`capture_161922_perf`；zip 文件名由脚本按生成时间创建。

## 必须包含

每个压缩包至少包含：

| 文件 | 要求 |
|------|------|
| `analysis.md` | 本轮分析结论，Markdown 格式 |
| `manifest.json` | 包内文件清单、生成时间、来源路径 |
| 过滤日志 | 优先使用 `Log/.filtered/filtered_*.log`，避免直接打全量日志 |
| 原始日志 | 必须包含对应 `ios_live_*.log`；默认放在包内 `raw_logs/` |
| trace 相关文件 | 有 Instruments 时必须包含 `.trace` 本体目录，以及 `_toc.xml`、`_hangs.xml`、`_hang_risks.xml` |

如果没有 trace，只打包 Markdown + 过滤日志 + 对应原始日志，并在 `analysis.md` 中说明
“本次未包含 Instruments trace”。不要把“只有 toc/hangs，没有 `.trace` 目录”的包称为完整导出包。

## Markdown 内容规范

`analysis.md` 应包含：

1. 问题摘要：一句话说明用户复现的问题。
2. 采集信息：日志路径、trace 路径、采集开始/结束时间、设备名/进程 PID（能拿到时填写）。
3. 关键结论：按置信度从高到低列出。
4. 证据：引用日志时间点、trace hang/microhang 时间点、相关文件名。
5. 风险与下一步：说明还需要代码侧或 Instruments 调用栈确认的点。

不要把整段超长日志粘进 Markdown；只摘关键时间点和文件路径。完整证据放入包内日志文件。

## 标准打包命令

优先使用本 Skill 附带脚本，避免手工漏文件：

```bash
python3 skills/ios-analysis-package/scripts/package_analysis.py \
  --tag capture_161922_perf \
  --report /path/to/analysis.md \
  --log "$HOME/Desktop/TraceCite/Log/.filtered/filtered_demo.log" \
  --raw-log "$HOME/Desktop/TraceCite/Log/ios_live_DemoPhone.log" \
  --trace "$HOME/Desktop/TraceCite/Instrument/capture_demo.trace" \
  --extra "$HOME/Desktop/TraceCite/Instrument/capture_demo_toc.xml" \
  --extra "$HOME/Desktop/TraceCite/Instrument/capture_demo_hangs.xml" \
  --extra "$HOME/Desktop/TraceCite/Instrument/capture_demo_hang_risks.xml" \
  --json
```

脚本会：

- 创建 `~/Desktop/TraceCite/analysis/export_<time>/`
- 默认自动包含 trace：优先使用 `--trace`，其次通过 `_toc.xml` / `_hangs.xml` 同名前缀反推 `.trace`，最后取 `~/Desktop/TraceCite/Instrument/` 下最新 `.trace`
- 默认自动包含原始日志：优先使用 `--raw-log`，其次从过滤日志头部 `# original_source:` 反推原始日志，并放入包内 `raw_logs/`
- 复制 Markdown、过滤日志、原始日志、trace 与 extra 文件
- 写入 `manifest.json`
- 生成同名 `.zip`
- 打印压缩包绝对路径

## Agent 执行约定

1. 用户要求打包时，**必须实际生成** Markdown 文件和 zip 压缩包，不要只给命令。
2. 如果本轮分析已经有结论，先把结论写入 `analysis.md`，再打包。
3. 必须同时打包过滤日志和对应原始日志；过滤日志用于快速阅读，原始日志用于复盘补证据。
4. `.trace` 是目录，默认必须递归打包进 zip；不要只打 `_toc.xml` / `_hangs.xml` 摘要文件。
5. 打包完成后，把 zip 的绝对路径告诉用户，让用户保存/转发该压缩包。
6. 如果某个输入文件不存在，先确认路径或改用实际存在的对应文件，不要生成缺证据的包。

## 示例回复

```text
已打包完成：
$HOME/Desktop/TraceCite/analysis/export_20260626T1630.zip

包内包含 analysis.md、过滤日志、原始日志、trace、toc、hangs 和 manifest.json，可以直接保存或发给研发同学。
```
