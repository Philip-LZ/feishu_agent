# 第六章：日志、指标、Trace 与敏感数据

本章围绕面试问题：“用户说 Agent 很慢或答错了，你怎么定位？如何把主、子 Crew 的执行关联起来？观测数据怎样避免泄露？”直接提供解释与答案。

## 1. 三种观测数据解决不同问题

| 数据 | 适合回答 | XiaoPaw 当前实现 |
| --- | --- | --- |
| 日志 Logs | 某次处理发生了什么，哪里抛异常？ | Python logging、Hook JSON、审计 JSONL。 |
| 指标 Metrics | 整体是否变慢、请求量怎样变化？ | Prometheus Counter、Histogram。 |
| Trace | 一次请求经过哪些步骤，各步骤怎样关联？ | Langfuse trace、generation、span。 |

用户说“刚才生成文档很慢”：指标帮助判断是否普遍变慢，trace 帮助找到耗时阶段，日志提供该阶段的错误细节。三者需要关联字段和正确的测量边界。

## 2. request trace ID 与 session ID 不同

入口生成 `InboundMessage.trace_id`；Runner 开始处理时把它绑定到 [`ContextVar`](../xiaopaw/observability/trace.py)。Python logging 的 formatter 读取这个变量，在每条日志上附加请求标识。

```text
routing_key = p2p:alice
session_id  = s-123
第一轮请求 trace_id = T1
第二轮请求 trace_id = T2
```

session 关联多轮对话，trace 关联一次请求。`shared_hooks/langfuse_trace.py` 的 `_get_trace_id()` 优先取外部请求 trace ID，没有时才回退到 session ID。因此正常 Runner 路径通常是“同一 session，多条 trace”；文件中的部分旧注释仍声称所有轮次共享一条 trace，不能照抄。

当前 Hook 的 structured log 主要写 session_id、turn 等字段，没有统一带请求 trace_id。adapter 每请求新建，turn 也不等价于跨请求唯一编号，所以仅靠这两项难以精确关联所有日志出口。

## 3. ContextVar 怎样跨协程与线程传播

ContextVar 让并发任务各自保存“当前请求是谁”，避免用一个普通全局字符串导致 A、B 请求互相覆盖。

普通线程池提交不应假设自动继承调用方的上下文。项目的 `run_in_executor_with_context()` 显式使用 `copy_context()`；SkillLoader 同步入口也用 `ctx.run(...)` 桥接到子线程。`asyncio.to_thread()` 本身会传播当前上下文。

传播的是上下文绑定。绑定值若是 adapter 这类可变对象，多个执行方仍可能持有同一对象；ContextVar 不是对象锁，也不是隔离容器。

Runner 当前最终把 trace ID 设回 `-`。它虽然保存过 set 返回的 token，但没有用 token 恢复旧值；这能清掉当前标识，却不能泛化为嵌套调用中的正确恢复。

## 4. 两套日志出口必须分别检查

[`logging_config.py`](../xiaopaw/observability/logging_config.py) 给 Python logging 配置：

- 控制台的人类可读格式，trace ID 只展示前 8 字符。
- 可选 `xiaopaw.log` JSON 行，包含完整 trace ID、级别、logger、消息和异常字符串。

这两种 formatter 都对 `record.getMessage()` 调用 `mask_pii()`。但 JSON 的 `exc` 字段直接取异常字符串，没有经过同一脱敏函数。

[`shared_hooks/structured_log.py`](../shared_hooks/structured_log.py) 则直接 `print(json.dumps(...), file=sys.stderr)`，绕过上述 formatter。例如工具输入先转字符串，再截前 200 字符写出。

截断只限制长度，不能保护其中的密码或 token。JSON 是输出格式，也不表示数据已经安全。

## 5. 指标要看定义，也要找更新位置

[`metrics.py`](../xiaopaw/observability/metrics.py) 定义 Counter 和 Histogram，使用少量标签，如 source、routing_type、model、status。

Counter 用来累计事件；Histogram 用 bucket 统计耗时分布，可用于聚合后估算分位数。不要只看平均值，长任务可能让少数用户等待很久。

`agent_latency` 在 Runner 成功路径上记录，从 `_handle()` 开始计时，因此：

- 包含会话读取、安全检查、Agent、回复及此前的处理时间。
- 不包含 dispatch 入队后的排队等待。
- 当前记录点没有覆盖全部拒绝和异常，也不包含随后 finally 的全部耗时。

所以不能把它直接说成“所有请求的用户端到端延迟”。

仓库定义了 `llm_calls_total`、`llm_latency`、`skill_timeout_total`，但本次搜索没有在其他业务代码中找到它们的更新调用。定义指标不等于已经接好埋点。

不要把用户 ID、trace ID 或完整 prompt 放进指标标签，否则每个值都会制造新时间序列。单请求细节应放在日志和 trace 中。

## 6. health 与 metrics 接口有什么含义

[`metrics_server.py`](../xiaopaw/observability/metrics_server.py) 提供 `/health` 与 `/metrics`。

`/health` 当前固定返回 `{"status":"ok"}`，只能说明该 HTTP 处理路径能响应，不会验证 LLM、沙箱、数据库或磁盘是否正常。

`/metrics` 在配置 `XIAOPAW_METRICS_TOKEN` 时检查 Bearer token，未配置时无需该 token。缺少 prometheus_client 时，指标对象退化成空实现，接口返回 501；业务继续运行不代表指标仍被采集。

## 7. Trace 怎样记录主、子 Crew

[`langfuse_trace.py`](../shared_hooks/langfuse_trace.py) 用事件创建和更新观测对象：

```text
请求 Trace（关联 session_id）
└─ 根 Span
   ├─ 模型调用 Generation
   └─ 工具调用 Span
      └─ 子 Crew 的模型/工具观测
```

这是阅读执行树的概念示意，具体父子关系由当前 parent id 和栈状态决定。

SkillLoader 同步入口先取父 skill span，再复制 ContextVar。子线程保留 trace 身份，重设子执行的根 span 并清理瞬时 generation、span 栈状态，让子观测挂在本次技能调用下。

当前子 Crew 没有与主 Crew 完全相同的模型前置 Hook 接线。仅凭继承 trace ID，不能证明子 Crew 的每次 LLM 调用都有完整输入、输出、token 和准确时间戳。自动收尾的 span 也可能表示兜底关闭，不能直接当成实际工具结束时刻。

## 8. flush 不是可靠投递保证

当前 Langfuse 事件先放进内存 batch buffer，flush 时取出并清空，按批发送。请求结束和 cleanup 会尝试 flush。

网络调用失败后，当前函数记录 debug 信息，没有把失败批次重新放回，也没有持久化重试队列。进程被强制终止也可能来不及清理。

因此“执行了 flush”不等于“远端已经可查询”，更不等于审计级不丢失。Handler 同步执行还意味着网络导出可能增加请求收尾耗时。

## 9. 四个出口的脱敏责任

业务可能需要用户原始文本，正确做法是在可观测副本写出之前清洗，不应为了日志打码而破坏业务输入。

| 出口 | 当前发现 |
| --- | --- |
| Python logging | 消息正文有手机号、邮箱、身份证规则；异常字符串未统一清洗。 |
| Hook JSON stderr | 工具输入预览截断后直接输出。 |
| Audit JSONL | 事件 kwargs 直接序列化，没有统一脱敏。 |
| Langfuse | 用户文本、工具参数等数据进入导出事件，没有统一 sanitizer。 |

[`mask_pii()`](../xiaopaw/observability/pii_mask.py) 没有覆盖通用密码、Authorization、API Key 等秘密字段。需要在每个独立出口的序列化或导出前做最小化、敏感字段删除/替换和必要内容检测。

通常保留 trace/session 标识、事件、工具名、结果、原因码、耗时；避免默认记录完整 prompt、文件正文、凭证和用户个人信息。验证时使用合成 secret，断言每个出口均不含原文，同时关键调查字段仍存在。

## 10. Audit 的名字不等于完整审计保证

[`SecurityAuditLogger`](../shared_hooks/audit_logger.py) 只有配置文件路径或 `SECURITY_AUDIT_FILE` 才写文件；当前 hooks.yaml 的默认空配置本身不提供持久化路径。

它的事件 deque 是共享实例内最近最多 10000 条记录。`session_end_handler()` 汇总整个 deque，却把当前 session_id 写在摘要上，没有按 session 筛选。因此摘要不能直接解释成“该会话独有的事件计数”。append-only 的代码写法也不提供外部防篡改保证。

## 11. 面试问题与直接答案

**问：怎么排查 Agent 很慢？**

答：先明确测量边界，分开排队、上下文准备、模型、技能/MCP、发送与导出耗时。用指标判断范围，用 trace 找阶段，用日志查错误。当前 agent_latency 不含排队等待，需要补足这段证据才能回答完整的用户延迟。

**问：日志里有 trace ID，为什么还要 trace 平台？**

答：日志帮助检索离散事件；trace 组织父子调用、时间关系和主/子任务。但只有埋点真实覆盖的部分才可用于归因。

**问：所有轮次共用同一 trace 吗？**

答：当前优先使用入口每请求生成的 trace ID，并用 session_id 关联多轮。没有外部 trace ID 时才回退到 session ID。

**问：日志写失败会阻断请求吗？**

答：当前 Registry 普通 dispatch 会捕获 handler 异常。它提高观测故障时的业务可用性，但不保证日志完整；关键审计是否允许失败放行需要明确业务要求。

**问：怎样证明已经脱敏？**

答：把合成 secret 放进嵌套工具参数、消息和异常，捕获 stderr、文件、审计与导出 payload，断言都不存在原文。只测 mask_pii 函数或只看页面打码都不够。

**问：你现在能准确统计成本吗？**

答：还需验证真实 token usage 采集与事件字段接线，并使用匹配模型的价格。当前部分事件字段是默认 0，不能据此宣称精确成本。

## 本次验证

```bash
conda run -n agent-env python -m pytest \
  tests/unit/shared_hooks/test_structured_log.py \
  tests/unit/shared_hooks/test_audit_logger.py -q
```

结果：13 passed。它们验证局部日志/审计行为，不证明 Langfuse 网络导出成功、所有出口已脱敏或指标端到端有效。本次未修改运行代码。

下一章讲测试与可靠性：如何用无 LLM 测试验证确定性边界，再用集成和 E2E 验证框架、沙箱与模型行为。
