# 已掌握请求主链路的基本边界

用户能准确找出 `Runner` 的四个主要依赖，并在 Feishu Listener 与 TestAPI 中定位 `InboundMessage` 的归一化入口；也能识别 Agent 调用前的 trace、历史、thinking 与安全检查，以及调用后的发送和持久化。后续需要加强正常路径、异常路径与 `finally` 收尾的区分，并把队列、会话等确定性控制职责纳入完整架构描述。

## Evidence

用户完成第一课四项源码练习，并给出对应代码位置与职责说明。
