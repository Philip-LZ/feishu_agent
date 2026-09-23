# Runner 并发速查

## 隔离单位

```text
p2p:{open_id}
group:{chat_id}
thread:{chat_id}:{thread_id}
```

`routing_key` 决定 queue、worker 和活动 session 的归属，但它本身不是 session id。

## 核心状态

```text
_queues[key]     → 等待处理的消息
_workers[key]    → 该 key 唯一消费者
_queue_gen[key]  → worker 世代标记
_dispatch_lock   → dispatch 中三个字典的原子修改
```

## 当前保证

| 行为 | 机制 |
| --- | --- |
| 同 key 不重叠处理 | 单 worker 顺序 `await _handle()`。 |
| 不同 key 可并发 | 每 key 独立 worker。 |
| 等待消息有上限 | `asyncio.Queue(maxsize=...)`。 |
| 空闲资源被回收 | `wait_for(queue.get(), idle_timeout)`。 |
| shutdown 不再接收消息 | `_shutting_down` 检查。 |

## 当前限制

| 限制 | 代码事实 |
| --- | --- |
| 满队列静默丢消息 | 仅 warning，`dispatch()` 仍返回 `None`。 |
| 没有全局 Agent 并发上限 | 大量不同 key 可创建大量 worker。 |
| idle cleanup 存在竞态风险 | worker 清理未与 dispatch 使用同一把锁原子化。 |
| 并发测试证据不足 | 文档列出的专门 Runner 单测当前不在仓库中。 |

## 不要混淆

```text
RateLimiter：控制进入频率
Queue：控制单 key 积压
Semaphore：控制 Sender 出站并发
```

配套课程：[第二课](../lessons/0002-routing-queue-concurrency.md)。
