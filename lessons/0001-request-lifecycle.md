# 第一课：系统骨架与一次请求的生命周期

> **预计时间：** 20 分钟  
> **本课目标：** 看懂 XiaoPaw 的主干，而不是记住所有模块。学完后，你应能画出一条普通消息的路径，并解释每层负责什么。

## 1. 先建立一个模型

XiaoPaw 可以先分成两个世界：

```text
确定性控制世界                         非确定性 Agent 世界

入口 → 校验 → 排队 → 会话 ─────────→ Main Crew → Skill → Sub-Crew
                     ↑                         ↓
                 持久化 ← 回复发送 ←──────────结果
```

“确定性”表示行为主要由程序规则决定，例如队列是否已满、会话从哪个文件读取、异常走哪个分支。“非确定性”表示 LLM 会参与意图理解、技能选择和答案生成。

这个区分很重要：生产系统不能把消息顺序、安全边界和异常收尾交给 LLM 决定。

## 2. `main.py` 不是业务流程，而是装配入口

从 [`xiaopaw/main.py`](../xiaopaw/main.py) 开始，但不要逐行阅读。只找它创建并连接的对象：

```text
SessionManager
Sender（FeishuSender 或 CaptureSender）
AgentFn（build_agent_fn 返回的函数）
HookRegistry
Runner
```

随后它把 `runner.dispatch` 交给三个可能的入口：

- `FeishuListener`：生产环境的飞书 WebSocket 消息；
- TestAPI：开发和测试环境的 HTTP 请求；
- `CronService`：定时任务产生的内部消息。

这种文件通常称为 **composition root**：负责选择具体实现并把依赖接起来。真正的请求处理不应堆在这里。

## 3. 三种入口先变成同一种消息

入口的原始数据完全不同：飞书给事件对象，TestAPI 给 JSON，Cron 给任务记录。它们在进入核心流程前都会构造 [`InboundMessage`](../xiaopaw/models.py)：

```python
InboundMessage(
    routing_key=...,
    content=...,
    msg_id=...,
    sender_id=...,
    trace_id=...,
)
```

核心字段的意义：

| 字段 | 作用 |
| --- | --- |
| `routing_key` | 决定消息属于哪个会话和哪条串行队列。 |
| `content` | 用户或定时任务交给系统的文本。 |
| `msg_id` | 标识原始消息。 |
| `sender_id` | 安全审计和用户识别所需的来源信息。 |
| `trace_id` | 串起本次请求的日志与 trace。 |

这里使用的是“入口适配”思路：核心流程只认识 `InboundMessage`，因此新增入口时不必复制 Runner、会话和 Agent 逻辑。

## 4. `Runner` 是应用控制中枢

[`Runner.dispatch()`](../xiaopaw/runner.py) 不立即执行 Agent。它先按 `routing_key` 找到或创建一条有界 `asyncio.Queue`，把消息放进去，并确保该 key 有一个 worker。

```text
routing_key A → Queue A → Worker A → 一次处理一条
routing_key B → Queue B → Worker B → 一次处理一条
```

因此：

- 同一 `routing_key` 串行，避免同一会话的上下文和持久化顺序互相覆盖；
- 不同 `routing_key` 可以并行，避免一个慢用户阻塞所有人；
- 队列有最大长度，满时当前实现记录 warning 并丢弃新消息。

队列细节会在第二课展开。本课只记住：**Runner 决定何时执行，Agent 决定执行什么。**

## 5. 一条普通消息在 `_handle()` 中经历什么

忽略具体 Hook 名称，正常路径可以压缩成：

```text
1. 绑定 trace_id
2. 判断是否为斜杠命令
3. 获取或创建 Session
4. 创建本次请求的 Hook adapter
5. 读取历史并发送 thinking 状态
6. 做 Agent 执行前的安全检查
7. 调用 AgentFn
8. 发送回复
9. 追加本轮会话记录并写指标
10. finally 中清理 Hook 和 trace
```

其中第 7 步是关键边界：

```python
reply = await self._agent_fn(
    inbound.content,
    history,
    session.id,
    key,
    session.verbose,
)
```

`Runner` 只依赖 `AgentFn` 的函数签名，不直接构造 CrewAI 对象。这使测试可以注入一个返回固定字符串的假 Agent，而生产环境注入 `build_agent_fn()` 创建的真实实现。

## 6. Agent 世界内部发生什么

[`build_agent_fn()`](../xiaopaw/agents/main_crew.py) 返回一个闭包。每次调用时，它创建 `MemoryAwareCrew`，然后执行 `run_and_index()`：

```text
AgentFn
→ MemoryAwareCrew
→ Crew.akickoff()
→ Main Agent 理解意图
→ 直接回答，或调用 SkillLoaderTool
```

Main Agent 没有直接装入所有具体技能工具，只看到 `SkillLoaderTool` 提供的技能名称与描述。真正选中技能后，[`SkillLoaderTool`](../xiaopaw/tools/skill_loader.py) 才读取对应 `SKILL.md`：

- `reference` 类型：返回技能说明，让 Main Crew 继续推理；
- `task` 类型：创建 Sub-Crew，在受限工具或 MCP 沙箱中执行。

这叫 **progressive disclosure（渐进式披露）**：先暴露简短能力目录，使用时再加载详细实现，避免主 Agent 的上下文被全部技能说明占满。

## 7. 回复不是 Agent 直接发出去的

AgentFn 返回字符串后，控制权回到 Runner：

```text
AgentFn 返回 reply
→ Sender 更新 thinking card 或发送新消息
→ SessionManager 追加 user / assistant 记录
→ 指标与 AFTER_TURN
→ finally 清理
```

`SenderProtocol` 隔离了输出渠道：生产用 `FeishuSender`，TestAPI 用 `CaptureSender`。因此同一条核心流程既能服务真实飞书，也能在测试中把回复捕获成 HTTP 响应。

## 8. 不是所有请求都走完整路径

理解分支比背主链路更重要：

| 场景 | 在哪里分流 | 结果 |
| --- | --- | --- |
| `/help`、`/new` 等命令 | `Runner._handle()` 开头 | 不调用 Agent，直接回复。 |
| 正常消息 | Agent pre-flight 之后 | 调 Agent、回复并持久化。 |
| `GuardrailDeny` | 专门的异常分支 | 返回“安全策略拦截”，仍执行清理。 |
| 未知异常 | 通用异常分支 | 返回统一错误信息，仍执行清理。 |

因此 `finally` 是可靠性设计的一部分：成功、拒绝或异常都必须结束 trace 和 Hook 生命周期。

## 9. 本课速记

```text
入口适配：Feishu / TestAPI / Cron → InboundMessage
调度边界：Runner 按 routing_key 排队并执行 _handle
Agent 边界：Runner → AgentFn → MemoryAwareCrew
能力加载：Main Crew → SkillLoaderTool → reference / Sub-Crew
输出边界：Agent 返回 reply → Sender + SessionManager
横切能力：Hook、trace、metrics 围绕主流程，不替代主流程
```

## 10. 小练习

这不是模拟面试，只用于检查课程是否讲清楚。完成时可以看源码。

1. 在 [`main.py`](../xiaopaw/main.py) 找到 `Runner` 构造位置，写出注入给它的四个主要依赖。
2. 在 [`listener.py`](../xiaopaw/feishu/listener.py) 和 [`test_server.py`](../xiaopaw/api/test_server.py) 各找到一次 `InboundMessage(...)`。
3. 在 [`runner.py`](../xiaopaw/runner.py) 找到调用 `self._agent_fn(...)` 的位置，并说明调用前后各有哪两类工作。
4. 用一行话解释：为什么“飞书机器人调用 LLM”不足以描述这个系统？

完成后把答案或不理解的源码行发给我。我会做课程答疑，不进入模拟面试。下一课再专门学习 `routing_key`、队列、并发与背压。

## 延伸阅读

- 仓库设计线索：[`docs/01-architecture.md`](../docs/01-architecture.md)
- Python 官方资料：[asyncio queues](https://docs.python.org/3/library/asyncio-queue.html)
- 课程路线：[`reference/0001-course-roadmap.md`](../reference/0001-course-roadmap.md)
