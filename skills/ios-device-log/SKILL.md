---
name: ios-device-log
description: >-
  使用 tracecite-mobile 采集 iPhone 真机运行日志，并通过 snapshot、时间窗、preset
  和行为摘要生成可复核证据。用户要求抓取 iOS 日志、启动后台日志 session、过滤日志
  或还原操作链时使用。
---

# iOS 真机日志采集

## 前置条件

- macOS 已安装 Xcode，`xcrun devicectl` 可用。
- 已安装 `libimobiledevice`，`idevicesyslog` 可用：

```bash
brew install libimobiledevice
tracecite-mobile --version
```

## 项目配置

进入待分析项目，先检查配置：

```bash
tracecite-mobile profile show
```

若项目尚无 `.tracecite/config.json`：

```bash
tracecite-mobile profile init --json
```

让用户核对 `process_name`、`subsystem`、`attach_process`、`log_output_dir`、
`capture_output_dir` 和默认过滤 preset。项目知识写入
`.tracecite/knowledge.ios.json`，不要把项目词写进 TraceCite Mobile 源码。

## 设备选择

```bash
tracecite-mobile list --json
```

- 0 台：提示检查 USB、信任状态和 Xcode 配对。
- 1 台：可使用 `--no-interactive`。
- 多台：必须先请用户选择，再传 `--udid` 或 `--index`；不要自动选第一台。

## 标准流程

### 1. 启动后台 session

```bash
tracecite-mobile session start --udid <UDID> --date --json
tracecite-mobile session status --json
```

session 默认持续采集。分析时冻结快照，不要因为一次分析自动停止 session。只有用户明确
要求停止时才执行：

```bash
tracecite-mobile session stop --udid <UDID> --json
```

需要把日志同步显示在当前终端时，可改用前台采集：

```bash
tracecite-mobile stream --udid <UDID> --date
```

默认日志目录是 `~/Desktop/TraceCite/Log/`，实际路径以命令 JSON 输出或 profile 为准。

### 2. 冻结并过滤

优先直接读取当前 session，避免手工猜日志路径：

```bash
tracecite-mobile filter --from-sessions --snapshot --last 5m --preset system-lifecycle --json
```

也可对明确的日志路径过滤：

```bash
tracecite-mobile filter "$LOG" --snapshot --since "20:15:00" --until "20:16:00" --grep 'request|response|timeout' --json
```

后续只使用 JSON 返回的 `output_path`。`match_records=0` 时依次放宽 pattern、扩大时间窗、
增加采集；仍不足则明确写“证据不足”，不要把“零命中”解释成“没有问题”。

### 3. 还原行为链

```bash
tracecite-mobile behavior summarize "$FILTERED_LOG" --json
```

排查异常时，把同一时间窗内“用户做了什么”与错误、超时或状态变化对齐。只按行号抽查
关键证据，不整份读取原始日志或过滤日志。

## 中性场景成长示例

当项目出现新的任务流问题时，先创建场景，再追加稳定词和 marker：

```bash
tracecite-mobile grow scenario task-flow --title "Task flow"
tracecite-mobile grow term user-behavior 'task.started' 'task.completed' 'request.failed' --scenario task-flow
tracecite-mobile grow marker 'task.completed' --scenario task-flow --category task.completed --label 'Task completed'
tracecite-mobile filter "$LOG" --snapshot --last 5m --preset user-behavior --scenario task-flow --json
```

一次性请求 ID、用户文案和高频轮询词不要沉淀为全局 starter knowledge。

## Agent 约定

1. 必须实际执行 `tracecite-mobile`，并以命令 JSON 输出为事实来源。
2. 多台设备必须先让用户选择。
3. 必经 `filter --snapshot --json`；分析正在写入的日志时不得直接整份读取。
4. 默认同时生成行为摘要；若行为信号不足，需要明确说明。
5. 回复采用“结论 → 证据 → 详细输出”，详细输出只给 evidence/report 路径。
6. 分析结束后，把结论追加到
   `~/Desktop/TraceCite/analysis/conclusions/YYYY-MM-DD.md`。

## 常见故障

| 现象 | 处理 |
|------|------|
| `idevicesyslog` 不存在 | `brew install libimobiledevice` |
| 无设备 | 检查 USB、信任状态与 Xcode 配对，再运行 `tracecite-mobile list --json` |
| 多设备非交互报错 | 明确传 `--udid` 或 `--index` |
| session 假存活或停更 | 查看 `session status --json`，按提示停止并重开 |
| filter 零命中 | 放宽 pattern/preset 或时间窗，再扩大采集 |
