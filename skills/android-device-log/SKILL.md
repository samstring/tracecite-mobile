---
name: android-device-log
description: >-
  使用 tracecite-mobile CLI + adb 采集 Android 真机/模拟器运行日志（adb logcat threadtime），
  并用 filter（--snapshot / --last / --since）做通用定界分析。
  当用户要求采集/抓取/录制 Android 真机日志、运行时日志、logcat、日志排查过滤，
  或指定某台设备 serial/设备名取日志时使用。始终带 --platform android。
---

# Android 真机日志采集（tracecite-mobile CLI + adb）

## CLI 路径

使用已安装的独立命令 `tracecite-mobile`。开发环境可先执行
`python -m pip install -e .` 安装 console script。

## 依赖

```bash
brew install android-platform-tools   # adb
adb version
adb devices -l
```

确认 adb 在 PATH 中，模拟器已运行或真机已连接。

## 平台识别

**Agent 必须自动识别项目平台**：检查用户上下文判断是 Android 还是 iOS。

### 识别线索

| 线索 | 判断为 Android | 判断为 iOS |
|------|---------------|------------|
| 用户提到 | "Android"、"模拟器"、"adb"、"logcat"、"真机" | "iPhone"、"iOS"、"Xcode"、"真机" |
| 项目目录 | 含 `.gradle`、`app/src/main/java`、`build.gradle` | 含 `.xcworkspace`、`.xcodeproj`、`Podfile` |
| 项目配置 | `.tracecite/config.json` 中 `platform: "android"` | `.tracecite/config.json` 中 `platform: "ios"` 或无 `platform` 字段 |
| 默认 | — | 无明确线索时默认 iOS |

### 行动规则

- **确定是 Android** → 所有命令加 `--platform android`
- **确定是 iOS** → 不加 `--platform`（默认 iOS）
- **不确定时** → **先问用户**「这是 Android 还是 iOS 项目？」，不要猜

## 优先：项目配置（Agent 开干前）

1. 查工作区是否有 `.tracecite/config.json`（`tracecite-mobile profile show`）。
2. **没有则先提示用户配置**：
   ```bash
   tracecite-mobile --platform android profile init --json
   ```
   会生成隐藏目录 `.tracecite/`（`config.json` + `knowledge.android.json`）。
3. 请用户核对 `package_name`、`device_serial`、日志输出目录等字段。

## 依赖

```bash
brew install android-platform-tools   # adb
adb version
adb devices -l
```

确认 adb 在 PATH 中，模拟器已运行或真机已连接。

## 设备选择

| 已连接数量 | Agent 行为 |
|------------|------------|
| **0 台** | 提示检查 USB/模拟器、adb 授权，不启动 |
| **1 台** | 可直接 `--no-interactive` 采集该设备 |
| **≥2 台** | **必须先问用户**选设备；列出 serial、型号、序号 |

设备参数：

```bash
# 按 serial
--udid emulator-5554
# 按序号（list 输出）
--index 1
```

## 标准流程

### 1. 列出设备

```bash
tracecite-mobile --platform android list
tracecite-mobile --platform android list --json
```

### 2. 启动后台日志 Session

```bash
tracecite-mobile --platform android session start --udid <SERIAL> --date --json
```

**重要：session 约定**
- session 默认**长时间后台运行**，不自动 stop
- 分析用 `filter --snapshot` 快照，不干扰正在写入的文件
- **只在用户明确说"停止"/"关闭"时才执行 `session stop`**
- 状态查询用 `tracecite-mobile --platform android session status --json`

### 3. 分析过滤

```bash
# 最近 N 分钟 Android framework 状态
tracecite-mobile --platform android filter "$LOG" --snapshot --last 5m --preset android-system --json

# 指定时间窗崩溃
tracecite-mobile --platform android filter "$LOG" --snapshot --since "07-26 20:15:00" --until "07-26 20:16:00" --preset android-crash --json

# 自定义正则
tracecite-mobile --platform android filter "$LOG" --snapshot --last 10m --grep 'SYNC_REQUEST|SYNC_SUCCESS|SYNC_FAILED' --json
```

### 4. 输出文件命名

- **默认**：`android_live_{serial}_{YYYYMMDD_HHMMSS}.log`
- 产物目录：`~/Desktop/TraceCite/Log/Android/`

## 日志过滤管线

源日志是 `adb logcat -v threadtime` 格式（`MM-DD HH:MM:SS.mmm PID TID P TAG: msg`）。
所有排查走同一管线，场景只换 `--preset`：

```text
冻结(--snapshot) → 定界(--last/--since/--until) → 过滤(--preset/--grep) → JSON output_path
```

### 内置 Android Preset

| preset | 用途 |
|--------|------|
| `android-anr` | ANR / 主线程阻塞 |
| `android-crash` | 崩溃 / 异常 / 服务崩溃 |
| `android-startup` | 启动耗时 |
| `android-memory` | 内存增长 / OOM / GC |
| `android-network` | Android connectivity 与 DNS 系统信号 |
| `android-system` | ActivityManager / WindowManager 等 framework 状态 |
| `android-custom` | 项目自定义过滤入口 |

### 行为分析 + 场景

```bash
# 中性任务流场景（先创建 task-flow 并添加稳定词）
tracecite-mobile --platform android grow propose scenario task-flow --title "Task flow" --created-by agent-a --case-id run-1 --evidence evidence://run/1#manifest
# 用第二个独立案例 verify，再由不同人工审核人 promote；禁止直接 grow term/marker/learning/auto。
tracecite-mobile --platform android grow verify kc-DEMO --case-id run-2 --outcome support --verified-by agent-b --evidence evidence://run/2#manifest
tracecite-mobile --platform android grow promote kc-DEMO --approved-by human-reviewer
tracecite-mobile --platform android filter "$LOG" --snapshot --last 5m --preset android-custom --scenario task-flow --json
```

## 结论持久化（跨 Agent 可复核）

每次分析完成后，**必须**将结论写入日期文件，供本 Agent 或其他 Agent 后续追溯/核实。

### 写入位置

`~/Desktop/TraceCite/analysis/conclusions/YYYY-MM-DD.md`

### 写入格式

文件为**追加式**，每次分析追加一个条目（而不是覆盖），格式如下：

```markdown
## [HH:MM] <分析类型> — <简述>
- **时间窗口**: <过滤时间范围>
- **分析工具**: android-device-log / filter --preset ...
- **平台**: Android
- **结论**: <核心结论>
- **关键证据**: <精简断言列表>
- **源文件**:
  - 过滤: <filtered_path>
  - 原始: <log_path>
```

### 何时写入

- 正常结论：分析完成、回复用户后，立即追加
- 证据不足：也写入，标注「证据不足」及已试 scope/preset

### 跨 Agent 使用

其他 Agent（Cursor / Codex / Claude / WorkBuddy）可通过读取 `~/Desktop/TraceCite/analysis/conclusions/` 下的日期文件，快速回顾当天所有分析结论。读取时优先按日期筛选，再按时间戳定位。

## Agent 执行约定

1. **必须加 `--platform android`**，不得遗漏
2. 多台设备必须先问用户，**禁止**自动选第一台
3. session 默认长期运行，分析用 `--snapshot`，不自动 stop
4. 分析前必经 `filter --snapshot --json`，禁止整份读原始 `.log`
5. 回复结构：结论 → 证据 → 详细输出（只给路径）

## 故障排查

| 现象 | 处理 |
|------|------|
| adb: command not found | `brew install android-platform-tools` |
| device offline | `adb kill-server && adb start-server` 或重连 USB |
| unauthorized | 确认设备已授权（弹窗点"允许"） |
| 无 device | `adb devices -l` 检查；模拟器确认已启动 |
| `--last` 报错 | 确认日志格式为 threadtime |
| filter 无命中 | 放宽 preset/时间窗，或加宽采集再试 |
