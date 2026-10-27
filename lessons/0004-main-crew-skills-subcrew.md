# 第四章：Main Crew、SkillLoader 与 Sub-Crew

本章回答：“为什么要分主、子 Agent？技能怎样加载？一次任务在哪里推理、在哪里执行？”采用技术说明加练习答案的形式。

## 1. 先区分 Agent、Crew、Skill 和工具

| 概念 | 在本项目中的含义 |
| --- | --- |
| Agent | 带角色、目标、模型和可调用工具的执行主体。 |
| Crew | 组织 Agent 与 Task 执行的对象。当前主、子 Crew 都各自装配一个 Agent 和一个 Task。 |
| Skill | `SKILL.md` 描述的任务说明、操作规范及相关脚本。它本身不是一个正在运行的 Agent。 |
| Tool | 模型能够发起调用的接口，例如 `skill_loader`、MCP 文件工具。 |

所以不是“一个 Skill 永久对应一个子 Agent”。task 型 Skill 被选中时，代码临时创建一个 Sub-Crew 来执行。

## 2. Main Crew 负责理解和委派

[`MemoryAwareCrew.orchestrator()`](../xiaopaw/agents/main_crew.py) 构造主 Agent，注入 Bootstrap 和两个工具：

```python
tools=[skill_tool, IntermediateTool()]
```

主 Agent 可以直接回答简单问题；需要外部能力时调用 `skill_loader`。`IntermediateTool` 用于过程信息，具体技能不是一组直接暴露给主 Agent 的独立工具。

例如模型不能把 `baidu_search` 当成工具名调用，而应该生成：

```python
skill_loader(
    skill_name="baidu_search",
    task_context='{"query": "需要查询的内容"}',
)
```

这里的 JSON 是任务上下文的示例，不表示所有技能都有相同的参数格式。实际要求来自对应 `SKILL.md`。

## 3. SkillLoader 先展示目录，再提供详细说明

[`SkillLoaderTool._build_description()`](../xiaopaw/tools/skill_loader.py) 读取 `load_skills.yaml`，跳过禁用项和不存在的技能文件，从 frontmatter 提取 description，然后生成名称、类型、描述组成的目录，放进工具 description。

模型先看到这份简短目录，选中技能后，`_get_skill_instructions()` 才提取完整正文、替换 `{session_id}`、`{skill_base}` 等占位符，并追加目录和 routing key 信息。

这就是渐进式能力披露：完整操作说明在需要时才进入相关执行上下文，减少主 Agent 的输入负担。

细节：初始化目录时宿主程序已经读过技能文件来提取描述。“按需加载”主要指给模型披露说明的时机，不意味着磁盘文件此前完全没有被读取。

## 4. reference 与 task 的分流

`_execute_skill_async()` 有三条路径：

| 路径 | 实际行为 |
| --- | --- |
| `history_reader` | 特殊分支，直接用 Python 返回传入历史的分页结果。 |
| 普通 reference | 返回 `<skill_instructions>...</skill_instructions>`，供主 Agent 继续使用。 |
| task | 创建 Sub-Crew，再 `await crew.akickoff(inputs=...)`。 |

因此“调用 Skill”不一定意味着创建子 Agent。reference 提供知识或操作指导，task 才委派执行。

## 5. 用生成文档任务串起调用链

假设用户要求“把这段内容整理成 Word 文档”。下面描述合理的执行路径，具体模型是否选择该技能仍取决于推理结果。

```text
Main Crew 理解需求
→ skill_loader(skill_name="docx", task_context=...)
→ 读取 docx/SKILL.md
→ build_skill_crew()
→ 子 Agent 结合任务与技能说明调用 MCP 工具
→ 沙箱生成文件
→ Sub-Crew 返回执行结果
→ SkillLoader 把结果作为工具返回值交给 Main Crew
→ Main Crew 整理最终回复
→ Runner 发送给用户
```

子 Agent 不自动继承主 Agent 的全部聊天上下文。构造参数主要包括技能说明、任务上下文、session 目录和 MCP 地址。因此 Main Crew 必须把必要的输入、文件路径、输出要求写清楚；遗漏信息会导致子 Agent 猜测或失败。

Sub-Crew 没有接入第三章的 `save_session_ctx()`。它的内部消息不会由这条主 Crew 保存路径直接并入主 ctx；主 Agent 接收到的是工具返回结果。

## 6. 推理进程与沙箱执行要分开理解

[`build_skill_crew()`](../xiaopaw/agents/skill_crew.py) 在宿主服务里构造 Agent、LLM 和 Crew，并通过 `MCPServerHTTP(url=...)` 连接沙箱。

```text
宿主 XiaoPaw 服务：Main Crew、Sub-Crew、模型 API 调用
                            ↓ MCP
Docker 沙箱：文件操作、命令和脚本等工具执行
```

子 Crew 的 Python 对象和推理循环没有因为使用 MCP 就搬进 Docker。线程也不构成安全隔离。真正的执行权限依赖 MCP 服务、容器配置、挂载范围与安全策略共同限制。

同样，`/workspace/sessions/{sid}` 是目录组织方式，不能单凭这个路径就宣称操作系统级会话隔离。容器里还存在共享 workspace。

## 7. 同步与异步入口为什么不同

SkillLoader 提供 `_run()` 和 `_arun()`：

- `_arun()` 直接等待 `_execute_skill_async()`。
- `_run()` 创建一个单 worker 的线程池，使用 `contextvars.copy_context()` 传递当前上下文，在子线程创建事件循环并执行协程。

因此不能说“所有 Sub-Crew 都必然在线程池里运行”；线程桥接发生在同步入口，具体使用哪个入口需要结合实际框架调用路径核实。

`copy_context()` 传递当前 adapter 和 trace 信息，帮助子调用挂到同一棵观测树。它不会复制主 Agent 的整个 messages，也不会给共享 adapter 自动加锁。

同步路径调用 `future.result(timeout=300)`，但超时不会自动杀死运行中的线程；退出线程池的上下文管理器还会等待任务结束。因此当前实现不能证明任务一定在 300 秒内彻底终止。异步入口也没有这个 `future.result()` 超时。

## 8. 为什么分两层，又付出什么成本

| 收益 | 代价 |
| --- | --- |
| 主上下文无需携带全部技能正文 | 多一次任务交接，关键信息可能遗漏。 |
| 子 Agent 聚焦一个技能 | 增加模型调用、延迟与 token 消耗。 |
| 可独立组织执行环境与观测 | 增加 MCP 连接、回调、线程和清理责任。 |
| 扩展技能可沿用统一目录与加载器 | 技能说明、参数和返回值需要维护一致性。 |

如果任务只是一个固定 API 调用，普通确定性工具通常更直接。两层 Agent 更适合执行步骤需要结合技能说明动态决定的任务，不能仅凭“多智能体”就证明质量更高。

## 9. 三个实现细节决定回答是否准确

**第一，主、子输出校验不同。** Main Task 配置 `output_pydantic=MainTaskOutput`，字段是 `reply` 和 `used_skills`；Sub-Crew 只在 `expected_output` 中要求 JSON，没有配置对应输出模型，SkillLoader 最后返回 `str(result)`。因此子结果的结构主要依赖模型遵循提示，不能当成已经完成 schema 校验的接口。

**第二，参数存在不等于约束生效。** `build_skill_crew()` 有 `allowed_tools` 参数，但当前函数没有用它过滤 MCP 工具。技能 frontmatter 中写 `allowed-tools` 也不等于这里已实现工具授权；真正的安全策略要继续追到下一章的 Hook 和沙箱配置。

**第三，所谓“零编排”仍有运行时编排者。** 主 Agent 决定选什么技能、怎样组合结果。代码没有预先固定所有业务步骤，但 Main Crew 明确承担委派和协调职责。

## 10. 常见面试问题与直接答案

**问：Skill 和 Tool 有什么区别？**

答：Tool 是模型可调用的接口，Skill 是完成某类任务的说明和资源。在本项目中，模型通过统一的 SkillLoader 工具选择技能，再得到说明或触发 Sub-Crew。

**问：为什么不把所有工具直接给主 Agent？**

答：大量工具和说明会增加输入负担，也增加选错工具的可能。项目先给主 Agent 简短能力目录，再按需加载详细指令；代价是额外模型执行和任务交接成本。

**问：主、子 Agent 怎么通信？**

答：主 Agent 通过 `skill_name`、`task_context` 发起调用；宿主程序加载说明并构造子 Crew，子 Crew 返回结果，再作为工具响应交给主 Agent。这里没有专门的 Agent 消息总线。

**问：Sub-Crew 是否共享主 Crew 的上下文？**

答：它接收显式任务上下文和技能说明；ContextVar 另行传播观测与 Hook 状态。两者不是同一种上下文，不能混为一谈。

**问：为什么不使用固定工作流？**

答：任务种类和步骤由用户意图决定，运行时选择技能具有灵活性；步骤明确、对顺序和结果确定性要求高的流程，更适合固定工作流。当前主、子 Crew 内部均使用 `Process.sequential`，这也不代表整个应用采用预定义业务流程。

**问：你会怎样改进当前设计？**

答：先依据故障证据补齐子任务结果校验、可靠超时取消和工具授权边界，再衡量调用成本与成功率；不要先增加更多 Agent。

主要证据为上述仓库实现与 `xiaopaw/agents/config/`。本章未运行真实模型或沙箱，不把源码意图当成已经验证的框架运行行为。下一章讲 Hook、安全拒绝与 fail-open / fail-closed。
