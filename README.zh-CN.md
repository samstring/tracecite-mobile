<p align="center">
  <strong>TraceCite Mobile</strong>
</p>

<p align="center">
  <strong>TraceCite Evidence Runtime 的官方 iOS / Android 领域扩展。</strong>
</p>

<p align="center">
  <a href="#"><img alt="version" src="https://img.shields.io/badge/version-0.1.0-blue"></a>
  <a href="#"><img alt="license" src="https://img.shields.io/badge/license-MIT-green"></a>
  <a href="#"><img alt="python" src="https://img.shields.io/badge/python-3.10%2B-blue"></a>
  <a href="#"><img alt="平台" src="https://img.shields.io/badge/平台-iOS%20%7C%20Android-lightgrey"></a>
</p>

---

## 解决什么痛点

排查手机 App 问题时，四个反复出现的问题：

**日志是事后的。** 问题发生在手机上，但你只能在事后从 IDE 导出日志——故障时刻的上下文已经丢失。

**日志和行为是两套语言。** 日志写的是 `"[1234]: [UI] button tapped: checkout"`，但你要回答的是「用户在崩溃前做了什么操作」。

**卡顿需要不止一种数据。** App 卡死了，光看日志看不出是主线程阻塞还是内存压力。需要性能采样，但手动操作 xctrace 或 Perfetto 很繁琐。

**每次从头开始。** 同一个 App，类似的问题，每次都要重新想用什么关键词、看什么指标。没有积累。

## 安装

```bash
# 先安装 TraceCite，再安装 Mobile 扩展
pip install tracecite
pip install tracecite-mobile
tracecite-mobile profile init
```

iOS 需要 `idevicesyslog`（libimobiledevice）和 Xcode 命令行工具。Android 需要 `adb`（SDK Platform Tools）。

## 怎么用

```bash
# 1. 启动后台采集
tracecite-mobile session start --date

# ... 在设备上复现问题 ...

# 2. 分析事发前后 2 分钟的日志（先 seal 切段，再 filter）
tracecite-mobile seal --from-sessions --json
tracecite-mobile filter --from-sessions --last 2m --preset system-fault --json

# 3. 把日志行提升为用户行为事件
tracecite-mobile behavior summarize --from-sessions --json
```

实时日志默认保留最近 30 分钟作为 hot 窗口；采集器每 30 分钟执行一次归档检查，
把已过期的数据移入隐藏的内部 `.archive/`。日常只需使用 `archive list` 和
`archive pull`，不需要、也不建议直接管理该目录。

维护清理是显式操作：`clean analysis --before today` 只清理过期日志、性能产物
和未 pinned 的已完成分析运行；运行状态、锁、仍在采集/恢复中的产物以及损坏的
manifest 会 fail-closed 保留。归档证据默认不碰；先用
`clean analysis --include-archive --dry-run` 预览，实际删除必须同时指定
`--include-archive --yes`。

一次完整的排查跑下来，Agent 可以拿到：

- **时间窗内的结构化命中** — 哪一行、什么时间、匹配了什么关键词
- **用户行为事件流** — 「用户打开了设置页 → 点击了保存 → App 卡死」
- **完整的运行记录** — 输入被冻结、参数被记录，随时可复核

```bash
# 卡顿问题？同步录制性能采样
tracecite-mobile capture start --template "Time Profiler"
# ... 复现卡顿 ...
tracecite-mobile capture stop    # → 自动输出 trace 文件 + 卡顿摘要

# 忘了搜什么？用预设词表
tracecite-mobile filter app.log --preset system-lifecycle --json
tracecite-mobile filter app.log --preset network-http --json
tracecite-mobile filter app.log --preset memory-leak --json

# 保留通用 preset，同时补充本次事故关键词（按 OR 合并）
tracecite-mobile filter app.log --preset network-http --grep 'checkout|payment' --json
```

## Agent-native 契约

TraceCite Mobile 是**移动端证据采集扩展**，不是 planner、根因判定器或停止策略引擎。

Agent 负责：假设、排查顺序、因果解释、证据充分性、最终结论以及何时停止。
Mobile 负责：当前 host / platform / device 范围内的机械事实，以及经过明确授权的 live action。

因此：设备/进程/session 查询为空，只能说明当前范围内这次观察没有看到；不能直接升级成全局不存在。
`session start/stop` 或 App launch 成功，也只表示动作成功，不代表 App 健康、根因成立或证据已经充分。

通用证据检索、精确 materialize/replay、aggregate、traverse、verify、provenance、
RetrievalSession novelty 等语义统一由 TraceCite Evidence Runtime 负责。

完整约定见：

- [Agent 接入（简体中文）](docs/agent-integration.zh-CN.md)
- [Agent integration (English)](docs/agent-integration.md)
- [`skills/tracecite-mobile/SKILL.md`](skills/tracecite-mobile/SKILL.md)

## 相比直接丢日志给 AI

| | 丢日志给 AI | TraceCite Mobile |
|---|---|---|
| 采集方式 | 事后从 IDE 手动导出 | 后台实时采集，故障时刻不丢失 |
| 数据种类 | 只有文本日志 | 日志 + 性能采样，一条命令录制 |
| 分析输出 | 原始文本，AI 自己解析 | 结构化 JSON + 用户行为事件 |
| 用户行为 | AI 从日志字符串推断 | 自动提升为结构化行为事件 |
| 知识积累 | 无 | 可审计的项目本地 candidate / trusted knowledge |
| 可复现 | 两次分析中间步骤不同 | run manifest 记录参数、输入与证据路径 |

## 整体架构

TraceCite 主包提供 canonical Evidence Runtime 与 TraceCite Extension Protocol；
TraceCite MCP 可以把同一套 Evidence 语义投影给 MCP Host。Mobile 保持独立，
只贡献 iOS / Android 领域能力：

```text
External Agent / Host
        |
TraceCite Evidence Runtime ---- TraceCite MCP（可选 transport）
        |
tracecite-mobile
        |
iOS / Android devices
```

单纯 `import tracecite` 或 `import tracecite_mobile` 不会自动修改 Core registry。
需要由 Host 显式加载 extension；独立 `tracecite-mobile` CLI 会在命令分发前显式
host 同一个 declarative TraceCite Extension 声明。

<img src="architecture.svg" alt="Mobile 架构：设备层、分析层、知识层、插件层，底层为 TraceCite Evidence Runtime" width="100%"/>

`--platform ios|android` 通过同一能力契约切换平台。可用
`performance profiles` 查询当前平台的性能 profile；未声明的可选能力会明确失败，
不会静默降级。

```bash
tracecite-mobile --platform ios performance profiles --json
tracecite-mobile --platform android performance start --profile frame --json
```

## 自定义流程

**预设词表。** 通用 preset 可以直接复用；项目特有知识通过 candidate → verify → promote 的治理流程沉淀：

```bash
tracecite-mobile filter app.log --preset system-lifecycle --json
tracecite-mobile filter app.log --preset network-http --grep 'checkout|payment' --json
tracecite-mobile grow propose scenario task-flow --title "Task flow" \
  --created-by agent-a --case-id run-001 --evidence evidence://run/001#manifest
```

**场景文件。** 把确定性的采集/过滤/断言机械流程写成 JSON，团队共享、版本控制；
排查策略和因果结论仍由 Agent 负责：

```json
{
  "schema_version": 2,
  "name": "crash-investigation",
  "source": { "type": "file", "path": "sealed.log" },
  "parse": { "segmenter": "auto" },
  "filter": { "grep": "SIGABRT|SIGSEGV" },
  "assert": {
    "rules": [
      {
        "name": "has-sigabrt",
        "type": "count",
        "event": { "match": "SIGABRT" },
        "min": 1
      }
    ]
  }
}
```

```bash
tracecite-mobile scenario run crash-investigation.json
```

**知识治理。** Agent 发现先进入独立 candidate store；第二个独立案例负责 verify，
再由不同 reviewer 授权 promote：

```bash
tracecite-mobile grow suggest app.log --preset my-app
tracecite-mobile grow propose learning "Bounded evidence is required" \
  --created-by agent-a --case-id run-001 --evidence evidence://run/001#manifest
tracecite-mobile grow verify kc-ID --case-id run-002 --outcome support \
  --verified-by agent-b --evidence evidence://run/002#manifest
tracecite-mobile grow promote kc-ID --approved-by human-reviewer
tracecite-mobile grow doctor
```

旧的直接写入方式（例如 `preset add`、`grow term/marker/learning/playbook/auto`）
在 Agent CLI 中会被拒绝。candidate 与 trusted knowledge 物理分离保存在 `.tracecite/`，
不上传；trusted 文件发生未治理修改时会触发完整性门禁。

## 相关包

- [**TraceCite**](../tracecite-core/) — canonical Evidence Runtime 与 Extension Protocol。
- [Agent 接入](docs/agent-integration.zh-CN.md) — Mobile Agent / Host 完整契约。

## 许可证

MIT
