---
name: android-device-profile
description: >-
  使用 tracecite-mobile CLI + adb + Perfetto 采集 Android 真机/模拟器性能现场（Perfetto trace），
  并结合常驻日志做联合分析。
  当用户要求录性能、Perfetto、卡顿/帧率/内存/启动/网络性能分析，或结合日志和 trace 联合排查时使用。
  始终带 --platform android。
---

# Android 性能现场采集（tracecite-mobile CLI + Perfetto）

## 依赖

```bash
brew install android-platform-tools   # adb
adb version
```

设备需 Android 9+（Perfetto 内置）。模拟器 Perfetto v15+ 可用。

## 平台参数

**所有 Android 命令必须加 `--platform android`**。

## 标准流程

### 1. 列出设备

```bash
tracecite-mobile --platform android list --json
```

### 2. 启动 Perfetto 录制

```bash
# 默认模板（perfetto-frame）
tracecite-mobile --platform android capture start --udid <SERIAL> --json

# 指定模板
tracecite-mobile --platform android capture start --template perfetto-memory --udid <SERIAL> --json
```

### 3. 查询录制状态

```bash
tracecite-mobile --platform android capture status --json
```

### 4. 停止并拉取 trace

```bash
tracecite-mobile --platform android capture stop --json
```

产物（默认路径，以 JSON 为准）：
```
~/Documents/TraceCite/mobile/Android/instrument/
├── perfetto_{template}.pb
└── perfetto_{template}.meta.json
```

## 可用模板

| 模板 | 用途 | 采集内容 |
|------|------|----------|
| `perfetto-frame` | 渲染帧率 / 卡顿 | sched + gfx/view/surfaceflinger atrace |
| `perfetto-memory` | 内存分析 | sched + mm/ion/ion stat atrace + meminfo |
| `perfetto-startup` | 冷/热启动 | sched + start/binder atrace |
| `perfetto-network` | 网络性能 | sched + net/wifi atrace |

## 日志联合分析

Perfetto 录制期间日志 session 也在后台运行时，可联合分析：

```bash
# 1. 查看录制期间的行为
tracecite-mobile --platform android filter "$LOG" --seal-first --last 5m --preset android-system --json

# 2. 查看录制期间的性能上报
tracecite-mobile --platform android filter "$LOG" --seal-first --last 5m --preset android-network --json
```

## Agent 执行约定

1. **必须加 `--platform android`**
2. 录制前确认：设备已连接、日志 session 是否在运行（`session status --json`）
3. 重复 start / 未录制 stop / 设备断开 均有错误提示
4. 第一版只做可靠采集和元数据，不解析 protobuf trace 内容（可用 Perfetto Web Viewer 查看）
5. 回复结构：结论 → 证据 → 详细输出（trace 路径 + manifest）

## 证据路径

分析 run 默认在 `~/Documents/TraceCite/mobile/Android/runs/`；trace 在 `instrument/`。回复给 manifest 与 artifact 路径即可。

## 故障排查

| 现象 | 处理 |
|------|------|
| "The trace config is invalid" | 确认 perfetto 版本和 `--txt` 参数 |
| "已有进行中的录制" | 先 `capture stop` 再 start |
| 低版本 Android | Perfetto 需要 Android 9+ |
| trace 文件为 0 字节 | 确认设备有足够空间；重试 |
