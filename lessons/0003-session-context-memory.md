# Session、LLM 上下文与长期记忆

本文针对的问题是：“Agent 怎样记住之前的对话？为什么既存聊天记录，又保存上下文，还需要长期记忆？”

## 路由与并发练习答案

1. 三个 key 是 `p2p:ou_123`、`group:oc_456`、`thread:oc_456:ot_789`。
2. 队列满时 `logger.warning()` 记录日志后返回，不抛异常。成功和拒绝都返回 `None`，调用方无法通过返回值区分。
3. 全局锁保护短暂的调度状态修改；Agent 执行在 worker 中，未持有该锁。
4. 需要协调的是“投递消息并确保有消费者”与“消费者退出并清理队列”。之前答案说使用同一把锁还不够完整：如果 dispatch 先入队，退出中的 worker 后拿锁仍直接删队列，消息依然丢失。退出方还需检查是否有新消息，继续消费或明确交接给新 worker。generation 相等也不能证明队列为空。
5. 当前未找到文档列出的五个 `test_runner_*` 专门测试；存在 `tests/e2e/test_e2e_02_routing.py`，但它不能代替全部生命周期测试。

## 1. 三种数据回答三个不同问题

以“我叫小林，请帮我查资料”为例：

| 数据 | 回答的问题 | 当前实现 |
| --- | --- | --- |
| Session 与业务历史 | 这是哪段会话？用户和助手说过什么？ | `sessions/index.json`、`sessions/{sid}.jsonl` |
| LLM 上下文 | 下一次调用模型时，具体发哪些消息？ | `ctx/{sid}_ctx.json`，恢复后再裁剪/压缩 |
| 长期记忆 | 换了会话后，哪些事实或历史还能找回来？ | workspace Markdown；可选 pgvector 索引 |

路径以配置的 `data_dir`、`ctx_dir`、`workspace_dir` 为根。持久化表示数据留在磁盘，不意味着模型每次都会看到它；必须有读取并加入输入的步骤。

## 2. Session：路由下的一段对话

[`SessionManager`](../xiaopaw/session/manager.py) 用 index 保存 `routing_key → active_session_id`，并保留该路由的 session 列表。

`get_or_create(key)` 找到活动会话就复用，没有才创建。`/new` 调用 `create_new_session(key)`，创建新 ID 并切换活动会话；旧文件不会因此被删除。

每轮正常回复发送后，Runner 调用 `append()`，把 user 和 assistant 各写一条 JSONL。它保存的是业务对话，不是完整的工具调用过程。

`load_history()` 默认最多返回最近 20 轮对应的 40 条记录。当前实现先读整个文件再截取尾部，所以返回条数有限并不意味着读取开销也有界。

## 3. 上下文：模型本次能看到的输入

每次请求都会创建 `MemoryAwareCrew`。跨请求的连续性依靠文件恢复，而不是依靠上一次 Crew 对象一直活着。

在 [`main_crew.py`](../xiaopaw/agents/main_crew.py) 的 `before_llm_hook()` 中，本请求第一次调用模型前会执行 `_restore_session()`，读取 `{sid}_ctx.json`，组合当前 system 消息、筛选后的旧上下文和本轮 user 消息。随后每次调用模型前都会裁剪工具结果、检查是否需要压缩。

业务历史和 LLM 上下文有两条不同的读取路径：

```text
sessions/{sid}.jsonl → Runner.load_history → SkillLoader.history_reader
ctx/{sid}_ctx.json   → MemoryAwareCrew._restore_session → 模型 messages
```

因此变量叫 `history_all` 也不表示它一定包含全部历史：当前 Runner 传入的来自默认有条数限制的 `load_history()`。

`run_and_index()` 在 Crew 返回后保存捕获到的 messages 到 ctx 文件，并把它识别出的新增片段追加到 `{sid}_raw.jsonl`。这与 Runner 后续保存业务 user/assistant 对话是不同操作；不要把 raw 文件直接宣传成已验证完整、不可丢失的审计档案。

## 4. 为什么必须控制上下文大小

工具可能返回大量网页、代码或搜索结果。全部保留会增加输入长度、成本和处理时间，还可能超出模型窗口。

[`context_mgmt.py`](../xiaopaw/memory/context_mgmt.py) 使用两步处理：

- `prune_tool_results()`：在较旧消息中，把长度超过 200 字符的 tool 内容缩成前 100 字符加标记。`keep_turns=10` 实际按最近 20 条消息划界，不是精确识别十轮完整的 Agent 交互。
- `maybe_compress()`：默认在估算 token 数达到窗口的 45% 时尝试压缩旧消息，保留近期部分，并调整切分位置。默认压缩器从 user/assistant 内容抽取截断片段，不调用 LLM 生成摘要；也不保证一次压缩后一定低于窗口限制。

这里要区分设计意图与实现保证：代码试图保护工具调用配对，但边界是否正确仍需专门测试。

还有一处源码不一致：压缩器生成的 system 摘要前缀为 `[Earlier conversation summary]`，而恢复逻辑只保留带 `<context_summary>` 的历史 system 消息。因此默认摘要在下一次请求恢复时可能被过滤。这是静态代码证据，本文未做运行复现。

## 5. 长期记忆如何跨越 `/new`

[`build_bootstrap_prompt()`](../xiaopaw/memory/bootstrap.py) 读取 workspace 下的 `soul.md`、`user.md`、`agent.md`、`memory.md`，拼入 Agent 的 backstory。它们不按 session id 命名，所以新 session 仍能读到这些文件中的信息。

例如，把“小林偏好中文回答”保存到 `user.md` 后，新会话仍可能利用该偏好。反过来，如果名字只存在旧 session 的上下文里，`/new` 后不会再从旧 ctx 自动恢复。

当前 Bootstrap 只读上述四个固定文件，`memory.md` 最多读取 250 行。memory-save 技能可以写 `memory_<name>.md`，但该类主题文件不会自动被这个函数读取；仍需其他读取路径。写入成功与下次可召回是两件事。

当前装配传入同一个 workspace，Bootstrap 本身没有按用户过滤这些文件；面试中不能把它描述成已经实现的用户级长期记忆隔离。

## 6. 向量记忆补充历史检索

当前 Runner 在成功保存业务对话后，若配置了数据库 DSN，会创建后台 `async_index_turn()` 任务，并放入 `_pending_index_tasks` 管理。

[`indexer.py`](../xiaopaw/memory/indexer.py) 调模型生成摘要、计算向量，再写入 pgvector 的 `memories` 表。`search_memory` 技能提供按需检索入口。它需要数据库配置、索引成功和检索调用等环节共同成立，不能只凭存在数据库表就宣称“永久记忆”。

索引异步执行意味着回复可以先返回，记忆稍后才可搜索；索引失败会记日志，当前这条路径没有持久化重试队列。

## 7. 面试问题与直接答案

**问：为什么聊天记录和上下文分开？**

答：业务历史保存用户输入和最终回复；模型上下文服务于推理，可能包含工具消息，并会裁剪或压缩。两者服务的用途不同，不能假定逐条一致。

**问：`/new` 是不是清空所有记忆？**

答：它切换活动 session，下一次请求使用新 session 的历史和 ctx。它不删除旧文件，也不清空 workspace 或数据库中的长期信息。

**问：为什么新建 Crew 仍能多轮对话？**

答：以稳定的 session id 读取已保存的 ctx，再在模型调用前恢复 messages。连续性来自外部状态恢复。

**问：怎样控制上下文增长？**

答：先缩短旧工具结果，再依据 token 估算尝试压缩旧消息。代价是信息损失；当前默认摘要是截断抽取，还存在跨请求摘要标记不一致的问题。

**问：文件记忆和向量记忆有什么区别？**

答：固定 Markdown 文件在构建 Agent 时主动注入，适合精简偏好和规则；向量库按需查找相关历史，适合较多的过去对话。两者都需要考虑更新、召回与访问范围。

下一章进入 Main Crew、SkillLoader 与 Sub-Crew：解释为什么分两层、技能如何按需加载，以及与直接调用工具相比增加了哪些成本。本章主要第一手材料是上述仓库源码；本次只更新文档，没有运行模型、数据库或端到端测试。
