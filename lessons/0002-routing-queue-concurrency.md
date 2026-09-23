# 第二课：`routing_key`、队列与并发边界

> **预计时间：** 25 分钟  
> **本课目标：** 理解 XiaoPaw 为什么既不是全局串行，也不是所有消息完全并行；能从当前源码判断它真正保证了什么、没有保证什么。

## 1. 先确定并发单位

Agent 请求通常很慢。如果所有消息共用一个 worker，一个用户的长任务会阻塞其他用户；如果每条消息都直接创建任务，同一会话的两轮消息可能同时读取旧历史，并以相反顺序写回。

XiaoPaw 选择的中间方案是：

```text
同一个 routing_key：串行
不同 routing_key：并行
```

[`resolve_routing_key()`](../xiaopaw/feishu/session_key.py) 定义了飞书消息的隔离粒度：

| 场景 | key 格式 | 隔离含义 |
| --- | --- | --- |
| 私聊 | `p2p:{open_id}` | 每个用户一条顺序流。 |
| 普通群聊 | `group:{chat_id}` | 同一个群共享一条顺序流。 |
| 话题线程 | `thread:{chat_id}:{thread_id}` | 同群不同 thread 可独立执行。 |

`routing_key` 不是 session id。它先定位一个路由，`SessionManager` 再为该路由保存当前活动 session；执行 `/new` 会在同一个 routing key 下切换 session。

## 2. Runner 保存了三组状态

在 [`Runner.__init__()`](../xiaopaw/runner.py) 中找到：

```python
self._queues: dict[str, asyncio.Queue[InboundMessage]] = {}
self._workers: dict[str, asyncio.Task] = {}
self._queue_gen: dict[str, int] = {}
self._dispatch_lock = asyncio.Lock()
```

可以把它们理解为同一张表的三列：

| key | queue | worker | generation |
| --- | --- | --- | --- |
| `p2p:alice` | Alice 待处理消息 | Alice 唯一消费者 | worker 世代编号 |
| `p2p:bob` | Bob 待处理消息 | Bob 唯一消费者 | worker 世代编号 |

`_dispatch_lock` 是全局锁，但它只包住很短的字典检查、入队和 worker 创建，并不包住耗时的 Agent 调用。因此它不会把所有 Agent 请求变成全局串行。

## 3. `dispatch()` 做了什么

当前代码顺序是：

```text
若正在 shutdown → 拒绝
取得全局 dispatch lock
→ 没有该 key 的 queue：创建有界 Queue
→ queue 已满：记录 warning 并丢弃新消息
→ 放入消息
→ 没有活 worker：generation +1，并 create_task
释放 lock
```

注意 `dispatch()` 在消息入队后就返回，它不等待 Agent 完成。真正的处理发生在后台 worker 中。

这也是 TestAPI 为什么还要等待 `CaptureSender` 的 Future：`await runner.dispatch(...)` 只代表成功提交，不代表已经得到回复。

## 4. 为什么同 key 串行、不同 key 并行

每个 worker 的核心循环是：

```python
while True:
    inbound = await queue.get()
    await self._handle(inbound)
```

一个 worker 只有完成当前 `_handle()` 后才会取下一条，所以同一 key 的处理不会重叠。

不同 key 拥有不同 worker。Worker A 等待 Agent 时会把事件循环控制权交还给 asyncio，Worker B 可以继续运行。因此这里的“并行”更准确地说是单事件循环内的 **concurrency（并发）**；它不意味着 Python 代码在多个 CPU 核上同时执行。

## 5. 有界队列就是背压策略

`max_queue_size` 默认是 10。它限制的是“正在等待的消息”，不包含 worker 已经取走并正在处理的那一条。

假设 Agent 正在处理 A0，队列上限为 2：

```text
A0：执行中
A1：进入 queue
A2：进入 queue，queue 满
A3：被丢弃并写 warning
```

当前策略的优点是实现简单、内存有上限；缺点是调用方没有收到明确的 overload 结果，也没有自动重试。生产上至少还需要把丢弃计数做成指标，才能知道系统是否持续过载。

不要把三种机制混在一起：

| 机制 | 控制什么 |
| --- | --- |
| Listener `RateLimiter` | 单位时间内允许多少入站请求。 |
| Runner 有界 Queue | 某个 routing key 能积压多少待处理消息。 |
| Sender `Semaphore` | 同时能发多少个飞书出站请求。 |

## 6. worker 为什么要空闲退出

如果每个出现过的 routing key 永久保留 queue 和 worker，长期运行后字典会无限增长。Worker 使用 `asyncio.wait_for(queue.get(), timeout=idle_timeout)`；默认空闲 300 秒后退出，并清理三组状态。

这解决资源回收，但引入了最难的一类并发问题：**新消息到达与旧 worker 退出同时发生。** `_queue_gen` 的设计目的就是让旧 worker 不要误删新一代 queue。

## 7. 文档意图与当前实现并不一致

[`docs/05-concurrency.md`](../docs/05-concurrency.md) 描述的目标实现是：worker 清理时也取得 `_dispatch_lock`，并用持久递增的 generation 判断自己是不是当前世代。

当前 [`runner.py`](../xiaopaw/runner.py) 的实际情况是：

- `dispatch()` 在锁内修改三个字典；
- worker 的 `finally` 没有取得同一把锁；
- 新建 queue 时 generation 被重新设为 `0`；
- 仓库没有文档所列的 `test_runner_queue_gen_race.py` 等专门单元测试。

因此，当前 generation counter **不能单独证明清理竞态已经解决**。从源码可推导出一种风险交错：

```text
1. worker 的 queue.get() 已经超时，但 worker 尚未执行 finally
2. dispatch 抢先运行，把新消息放入旧 queue
3. dispatch 看到旧 worker 任务尚未 done，因此不创建新 worker
4. 旧 worker 执行 finally，删除 queue 和 worker
5. 新消息留在无人持有的旧 queue 中
```

这是基于当前代码的静态分析结论；由于当前环境没有安装 pytest，本课没有把它写成可执行回归测试。面试学习中最重要的不是假装项目没有缺陷，而是能准确说清：设计意图、当前证据和残余风险分别是什么。

## 8. 现有测试证明到哪里

[`tests/e2e/test_e2e_02_routing.py`](../tests/e2e/test_e2e_02_routing.py) 当前证明了：

- Alice 与 Bob 的 session 隔离；
- 三个不同 routing key 的消息不会串话。

它没有直接证明：

- 同一 key 的十条消息严格按顺序完成；
- 队列满时的丢弃行为；
- idle timeout 附近的新消息不会丢失；
- 一个 `_handle()` 异常后，队列剩余消息仍被消费。

测试名称出现在设计文档里，不等于测试文件真实存在。以后阅读 `docs/` 时，都要回到 `tests/` 目录核对。

## 9. 本课速记

```text
routing_key = 并发与顺序的隔离单位，不是 session id
每 key 一个 Queue + 一个 Worker = 同 key 串行、跨 key 并发
dispatch 只负责提交，_worker 才负责执行
有界 Queue = 局部背压；当前满载策略是 warning + drop
idle timeout = 回收资源，但带来退出/新消息竞态
文档描述设计意图；源码和真实测试决定当前保证
```

## 10. 小练习

可以看源码完成，不是模拟面试。

1. 分别写出私聊用户 `ou_123`、群聊 `oc_456`、群内 thread `ot_789` 的 routing key。
2. `max_queue_size=2`，某 key 已有一条执行中、两条等待中；下一条消息在当前实现中会发生什么？调用方能否从 `dispatch()` 返回值知道它被丢弃？
3. 为什么 `_dispatch_lock` 是全局锁，却没有让不同 routing key 的 Agent 执行变成串行？
4. 对照 `runner.py`，复述第 7 节竞态中的五个步骤。你认为真正需要原子化的是哪两类操作？
5. 在 `tests/` 中确认文档列出的五个 Runner 并发单元测试是否真实存在。

完成后把答案发给我。我会先做课程答疑，再进入第三课 Session、上下文与记忆。

## 延伸阅读

- 当前实现：[`xiaopaw/runner.py`](../xiaopaw/runner.py)
- routing key：[`xiaopaw/feishu/session_key.py`](../xiaopaw/feishu/session_key.py)
- 设计文档：[`docs/05-concurrency.md`](../docs/05-concurrency.md)
- Python 官方资料：[asyncio queues](https://docs.python.org/3/library/asyncio-queue.html)
- 本课速查：[`reference/0002-runner-concurrency-cheatsheet.md`](../reference/0002-runner-concurrency-cheatsheet.md)
