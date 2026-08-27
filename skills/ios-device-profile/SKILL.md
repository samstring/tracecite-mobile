---
name: ios-device-profile
description: >-
  使用 tracecite-mobile 和 Xcode Instruments 采集 iPhone 真机性能现场，并将 trace
  与同一时间窗的设备日志、行为摘要联合分析。用户要求排查卡顿、CPU、内存、启动、网络
  或动画掉帧时使用。
---

# iOS 性能现场采集

## 前置条件

- macOS 已安装完整 Xcode，`xcrun xctrace` 与 `xcrun devicectl` 可用。
- 目标 iPhone 已连接、信任并完成 Xcode 配对。
- 已执行 `tracecite-mobile profile init`，并核对 `attach_process`、
  `capture_output_dir` 与 `capture_template`。

```bash
tracecite-mobile profile show
tracecite-mobile list --json
xcrun xctrace list templates
```

多台设备时必须先请用户选择，再使用 `--udid` 或 `--index`。

## 模板选择

| 现象 | 模板 |
|------|------|
| CPU 高、卡顿、点击无响应 | `Time Profiler` |
| 主线程、锁或调度问题 | `System Trace` |
| 内存增长、疑似泄漏 | `Leaks` |
| 分配热点 | `Allocations` |
| 网络慢 | `Network` |
| 启动慢 | `App Launch`，同时使用 `--launch <BUNDLE_ID>` |
| 动画掉帧 | `Animation Hitches` |

用户未说明问题类型时，使用 profile 默认模板；不要凭空生成模板名称。

## 标准流程

### 1. 保留同窗日志

若日志 session 尚未运行，先启动：

```bash
tracecite-mobile session start --udid <UDID> --date --json
tracecite-mobile session status --json
```

### 2. 开始录制

attach 已运行的 DemoApp：

```bash
tracecite-mobile capture start --udid <UDID> --template "Time Profiler" --attach DemoApp --json
```

仅在明确需要重启应用采集启动过程时使用 `--launch`：

```bash
tracecite-mobile capture start --udid <UDID> --template "App Launch" --launch com.example.demo --json
```

让用户复现问题。录制期间可以查看状态，但不要重复 start：

```bash
tracecite-mobile capture status --udid <UDID> --json
```

### 3. 停止并导出

```bash
tracecite-mobile capture stop --udid <UDID> --json
```

默认 trace 目录是 `~/Documents/TraceCite/mobile/iOS/instrument/`；必须以 stop 的 JSON 输出为准，
记录实际 trace、toc、hang 摘要和分析路径。没有活动录制时不要反复 stop。

### 4. 联合日志分析

```bash
tracecite-mobile seal --from-sessions --json
tracecite-mobile filter --from-sessions --seal-first --last 5m --preset system-lifecycle --json
tracecite-mobile behavior summarize "$FILTERED_LOG" --json
```

根据问题再增加一条直接相关的过滤，例如网络：

```bash
tracecite-mobile filter --from-sessions --seal-first --last 5m --grep 'request.started|request.failed' --json
```

把复现操作、日志时间点与 trace 的录制区间对齐。若调用栈、行为链或故障信号不足，结论
必须标记为“证据不足”，不要仅凭模板名称推断根因。

## Agent 约定

1. 必须实际执行 `tracecite-mobile`，并保留 start/stop JSON 结果。
2. 多台设备必须先让用户选择；启动录制前确认目标 App 是否已运行。
3. 默认 attach 已运行进程；只有启动分析才使用 `--launch`。
4. trace 与日志必须使用同一复现时间窗；live hot 用 `seal` / `--seal-first` 后再 filter。
5. 回复采用“结论 → 证据 → 详细输出”，详细输出仅给 trace、manifest 与 evidence 路径。
6. 分析 run 默认写入 `~/Documents/TraceCite/mobile/iOS/runs/`。
7. 用户要求归档时，调用 `ios-analysis-package`，传入本轮真实 report、filtered log、
   raw log 与 trace 路径。

## 常见故障

| 现象 | 处理 |
|------|------|
| `xcrun` 或 `xctrace` 不存在 | 安装完整 Xcode，并确认 Command Line Tools 指向该 Xcode |
| 找不到进程 | 先启动 App，核对 `--attach` 或 profile 的 `attach_process` |
| 已有录制 | 先查询 `capture status --json`，确认后停止旧录制 |
| trace 未导出 | 检查设备连接、Xcode 权限和 stop JSON 错误 |
| 日志与 trace 对不上 | 重新采集同一复现时间窗，不拼接不同轮次证据 |
