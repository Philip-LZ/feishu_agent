# XiaoPaw AI Agent 面试学习 Resources

## Knowledge

- [运行入口](xiaopaw/main.py)
  当前实现的装配根：Sender、SessionManager、AgentFn、HookRegistry 与 Runner 在这里接线。用于：判断对象由谁创建、生命周期由谁管理。
- [请求调度实现](xiaopaw/runner.py)
  当前实现的确定性控制面：按 routing key 排队、会话处理、Agent 调用、回复、持久化与清理。用于：复建第一条可运行闭环。
- [核心契约](xiaopaw/models.py)
  `InboundMessage` 与 `SenderProtocol` 的真实定义。用于：区分稳定边界与具体入口/发送实现。
- [可执行测试](tests/)
  运行事实和回归证据。用于：验证文档主张、理解异常路径，并为亲手修改建立反馈环。
- [XiaoPaw architecture design](docs/01-architecture.md)
  本仓的系统边界、请求路径、并发和信任模型。用于：提出阅读假设；不能替代源码与测试证据。
- [XiaoPaw design decisions](DESIGN.md)
  本仓 ADR、任务生命周期与生产取舍。用于：理解设计意图，并与当前实现交叉核对。
- [CrewAI Crews](https://docs.crewai.com/v1.15.22/en/concepts/crews.md)
  Crew、kickoff 与生命周期回调的官方语义。用于：解释 Main/ Sub-Crew 和 callback 接线。
- [CrewAI Tools](https://docs.crewai.com/v1.15.22/en/concepts/tools.md)
  Agent 可调用能力和工具错误边界。用于：解释 SkillLoader 与工具执行失败。
- [MCP Architecture](https://modelcontextprotocol.io/docs/learn/architecture)
  MCP host、client、server、transport 的官方边界。用于：区分 Crew 编排与沙箱服务执行。
- [Python asyncio queues](https://docs.python.org/3/library/asyncio-queue.html)
  有界异步队列的官方语义。用于：解释 Runner 的保序和背压。
- [Primary-source request-path findings](research/ai-agent-request-path-sources.md)
  上列外部资料与 XiaoPaw 第一课的对应结论；用于课后核对。
- [Security-log redaction primary sources](research/security-log-redaction-sources.md)
  OWASP、OpenTelemetry、Langfuse 与 NIST 对敏感数据最小化、出口前脱敏、访问和保留策略的权威指引。用于第二课与安全面试。

## Wisdom (Communities)

## Gaps

- 目前不把面试经验帖作为事实来源；后续仅在需要模拟题风格时再挑选可信社区材料。
