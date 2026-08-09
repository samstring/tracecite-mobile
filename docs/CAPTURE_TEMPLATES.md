# Instruments 模板选择约定

供 Agent 在 `tracecite-mobile capture` 时选用模板。用户未说明类型时默认 **Time Profiler**；提到「内存 / 泄漏」时用 **Leaks**。

| 用户描述 | `--template` | 说明 |
|----------|--------------|------|
| 卡顿、点击无反应 | `Time Profiler`（别名 `cpu`） | `capture start`，复现后 `stop` |
| 主线程 / 锁 / 调度 | `System Trace`（别名 `system`） | 同上 |
| 内存涨、疑似泄漏 | `Leaks`（别名 `leak`） | 操作一段时间后再 `stop` |
| 看谁分配了多少内存 | `Allocations`（别名 `alloc`） | 同上 |
| 接口慢、加载慢 | `Network`（别名 `network`） | 触发慢请求后 `stop` |
| 启动慢 | `App Launch`（别名 `launch`） | `--launch <BundleId>` 冷启动 |
| 动画掉帧 | `Animation Hitches`（别名 `hitch`） | 滑动/动画后 `stop` |

录制不设固定时长；最长 **2 小时** 自动停止，需再执行 `tracecite-mobile capture stop` 导出。

完整模板列表：

```bash
xcrun xctrace list templates
```
