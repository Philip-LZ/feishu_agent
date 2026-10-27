# 第五章：Hook、安全策略与拒绝生命周期

本章面试主题：“你怎样把安全检查接到 Agent？策略失败时怎么办？如何证明危险工具没有执行？”所有问题直接附答案。

## 1. Hook 是主流程中的扩展点

前四章介绍了消息、会话、Crew 和技能。Hook 把日志、权限、成本等公共逻辑接在这些流程的关键位置，避免每个技能都复制一套检查。

```text
Runner 开始本轮
→ Adapter 翻译成事件
→ Registry 按顺序调用 Handler
→ 日志/trace 或策略处理
→ 控制权回到主流程
```

这里并非独立消息队列：Registry 当前直接同步调用 Python handler。注释中的 fire-and-forget 主要表示不让普通观测异常传播，不表示 handler 自动在后台执行。

## 2. 四层结构各负责什么

| 层 | 文件 | 职责 |
| --- | --- | --- |
| 声明与装配 | `shared_hooks/hooks.yaml`、`hook_framework/loader.py` | 选择 handler，构造策略对象，解析依赖，按顺序注册。 |
| 框架适配 | `hook_framework/crew_adapter.py` | 将 Runner/Crew 回调转换为事件，维护 pending deny 与耗时。 |
| 事件分发 | `hook_framework/registry.py` | 决定按何种异常语义调用 handler。 |
| 具体策略 | `shared_hooks/*.py` | 输入检查、权限、预算、循环检测、重试观测与审计。 |

阅读入口：[HookLoader](../xiaopaw/hook_framework/loader.py)、[Adapter](../xiaopaw/hook_framework/crew_adapter.py)、[Registry](../xiaopaw/hook_framework/registry.py)。

## 3. 七类事件覆盖哪些边界

| 事件 | 当前主要触发点 | 用途 |
| --- | --- | --- |
| `BEFORE_TURN` | Runner 启动本轮；adapter 也可能根据 LLM 状态推断新 turn | 建立观测记录。 |
| `BEFORE_LLM` | 主 Crew 的模型调用前 Hook | 记录模型输入相关信息。 |
| `BEFORE_TOOL_CALL` | 虚拟 pre-flight 和工具调用前 | 检查工具输入、权限和预算。 |
| `AFTER_TOOL_CALL` | adapter 显式调用，包括拒绝记录 | 记录结果、耗时、循环或失败状态。 |
| `AFTER_TURN` | step callback 及 Runner 完成本轮 | 预算和重复状态检查、观测收尾。 |
| `TASK_COMPLETE` | 主 Crew 的 task callback | Task 完成记录与 pending deny 检查。 |
| `SESSION_END` | Runner finally 中的 adapter cleanup | 审计摘要与 trace flush。 |

事件名称不能直接当作“一请求恰好一次”的保证。尤其 `AFTER_TURN` 同时服务推理 step 和业务请求结束；`SESSION_END` 当前在每次请求清理时触发，不表示 SessionManager 的逻辑会话已经被删除或切换。

## 4. dispatch 与 dispatch_gate 的区别

两者遍历的是同一个事件下的已注册 handler 列表。不是“hooks 段只能走 dispatch，strategies 段只能走 gate”；真正决定语义的是调用方用了哪个方法。

| handler 行为 | `dispatch()` | `dispatch_gate()` |
| --- | --- | --- |
| 正常返回 | 继续下一 handler | 继续下一 handler |
| 抛 `GuardrailDeny` | 记录异常，继续 | 立即向上传播，后续 handler 不执行 |
| 抛普通异常，fail_closed=False | 记录异常，继续 | 记录异常，继续 |
| 抛普通异常，fail_closed=True | 仍记录异常，继续 | 转成 `GuardrailDeny`，向上传播 |

所以“配置了安全策略”还不够：必须核对其注册成功、事件真的触发、使用 gate 分发、拒绝最终阻止副作用。

## 5. fail-open 与 fail-closed

fail-open：检查器故障后继续业务，适合可容忍丢失的观测环节。fail-closed：检查器故障后拒绝业务，适合关键授权检查。

当前 `main.py` 将 `sandbox_guard`、`permission_gate` 标成 fail-closed。它只约束已注册 handler 在 `dispatch_gate()` 执行时抛普通异常的行为。

启动时若 YAML 解析、模块加载或策略实例化失败，Loader 的若干分支会记录错误然后返回或跳过。缺失的策略不会因为 fail_closed 标记自动补成拒绝器。因此当前不能宣称“任何安全组件故障都会阻止服务启动”。

## 6. 各策略检查什么

| 策略 | 当前行为与边界 |
| --- | --- |
| SandboxGuard | 归一化输入并用正则查路径穿越、危险命令和部分注入模式；不是完整命令语义分析器。 |
| PermissionGate | 按工具名查 allow/warn/deny；当前默认配置为 warn，没有用户级权限矩阵。 |
| CostGuard | 按事件 token 字段累计估算费用并检查预算；字段未正确接线时不能得出真实费用。 |
| LoopDetector | 比较连续的工具输出或 step 文本状态，达到重复阈值后抛 deny。 |
| RetryTracker | 记录连续失败和恢复，达到阈值写 warning；不会自动重试，也不阻断。 |
| AuditLogger | 记录安全事件，并在 cleanup 时生成摘要。 |

不要把“有策略类”当作整个链路已生效。例如 adapter 的某些 `AFTER_TOOL_CALL` 使用普通 dispatch，LoopDetector 在那里抛的 deny 会被吞掉；step 路径的 `AFTER_TURN` 则使用 gate，可保存拒绝。

默认 CostGuard 依赖 `ctx.input_tokens/output_tokens`，当前 step 路径构造的事件未填这些字段，默认值为 0。所以这条路径不能证明精确计费或有效预算硬停。

策略实例由启动时 Loader 构造，多个请求的 adapter 共用 Registry。CostGuard 的累计量、LoopDetector 的状态列表并未按 session 分桶；不能把它们描述成已经隔离的每用户预算和循环状态。

## 7. 第一条拒绝路径：pre-flight

Runner 在调用 AgentFn 前，把用户文本前 500 字符包装成虚拟工具调用：

```text
tool_name = agent_execution
→ adapter.on_before_tool_call()
→ Registry.dispatch_gate()
→ 策略抛 GuardrailDeny
→ adapter 保存 pending_deny
→ Runner 立即检查并重抛
→ 返回“安全策略拦截”
```

这条拒绝发生在 Agent 调用之前，可以避免进入后续 Agent 执行。它检查的只是前 500 字符，且模型后来可能生成新的工具参数，因此真实工具调用前仍需要检查。

## 8. 第二条路径：真实工具拒绝与 pending_deny

项目设计用 `_pending_deny` 暂存策略拒绝，等 step 或 task callback 再抛出，目的是避免框架把安全拒绝当作普通工具失败反复重试。

但“终止后续推理”和“阻止当前工具”是两个动作：

```text
执行前：拒绝 → 框架跳过当前工具
后续回调：抛出拒绝 → 结束剩余推理 → Runner 回复
```

当前 adapter 捕获 deny 后不向调用方返回阻断结果，主 Crew 的 before-tool Hook 最后返回 `None`。上一轮核对 agent-env 的 CrewAI 源码，工具执行帮助函数遇到 Hook 返回 `False` 才明确跳过工具。当前接线因此存在先执行工具、后抛拒绝的风险。

`pending_deny` 是传递拒绝原因的机制，本身不提供执行前阻断。日志里记录 denied 也不能证明文件没有写、消息没有发。此处是源码核对结论，尚未用真实 Crew 工具调用复现。

## 9. finally 收尾做什么

[`Runner._handle()`](../xiaopaw/runner.py) 区分安全拒绝和普通异常，然后在 finally 中调用 adapter cleanup。

cleanup 有 `_cleaned` 幂等标记，避免同一个 adapter 重复触发 `SESSION_END`。它负责触发审计和 trace flush，不提供文件回滚、消息撤回或数据库事务补偿。

当前执行顺序通常是先回复，再进入 finally。不能根据 cleanup 的旧注释宣称“用户收到回复前 trace 必定已上传”。flush 失败、进程被强制终止也不能靠 finally 保证数据永不丢失。

## 10. 如何证明安全真正生效

验证必须分层：

1. 策略单测：给定输入是否抛正确的 `GuardrailDeny`。
2. Registry 单测：gate 是否传播拒绝、fail-closed 是否生效。
3. 框架集成：注册拒绝策略，调用一个只计数的无害工具，断言工具计数保持 0。
4. Runner 集成：用户收到拒绝回复，cleanup 被调用，拒绝后同 key 的正常请求仍可处理。

第 3 层非常关键。只断言 `_pending_deny` 非空或看到“安全策略拦截”回复，都不能证明副作用没有发生。

本次运行：

```bash
conda run -n agent-env python -m pytest \
  tests/unit/hook_framework/test_hook_registry.py \
  tests/unit/shared_hooks/test_permission_gate.py -q
```

结果：24 passed。这验证了选定的局部单测，未覆盖真实 CrewAI 的执行前阻断。

## 11. 面试问答与答案

**问：为什么不用 prompt 约束工具权限？**

答：模型可能产生违规调用。权限需要在副作用发生前由确定性代码判断，并由工具执行层强制拒绝；prompt 负责指导，不能承担最终授权。

**问：为什么日志失败放行，授权失败拒绝？**

答：二者的故障代价不同。观测短暂故障通常不应中断业务；无法确定授权时继续执行可能造成不可撤销的副作用。但审计是否允许丢失还要依据具体业务要求决定。

**问：为什么拒绝用专门异常？**

答：区分策略决定与程序故障，让 Runner 给出明确回复并记录 reason code。专门异常仍需正确穿过框架，并与执行前阻断配合。

**问：为什么 handler 的顺序重要？**

答：gate 首次遇到拒绝就停止。当前观测 handler 先注册，可先留下尝试记录；CostGuard 在 AFTER_TURN 中先于 LoopDetector，避免后者拒绝导致前者没机会处理。但顺序不代替正确的数据与事件接线。

**问：有了 fail-closed 就足够安全吗？**

答：还要核对加载失败、入口绕过、身份传递、资源归属、框架阻断和底层权限。当前项目存在启动加载跳过、默认 warn、用户级授权缺失与执行前阻断缺口，不能宣传为完整的权限系统。

**问：怎样证明危险动作没发生？**

答：用无害的替身工具记录是否进入函数，走完整框架调用链，拒绝场景必须断言工具调用次数为 0，而不仅检查异常或日志。

下一章：日志、指标与 trace，解释如何关联一次请求，以及如何避免把敏感内容写进可观测系统。
