# AI-agent request path: primary sources

Scope: the first XiaoPaw request-path lesson—queueing a request, entering a Crew, invoking tools/MCP, and observing lifecycle callbacks.

## Findings

- **CrewAI Crew is the execution boundary.** A Crew groups agents and tasks and defines the execution process; the documented entry point is `crew.kickoff(inputs={...})`. Crew-level `step_callback` runs after each agent step, `task_callback` after each task, `before_kickoff_callbacks` before execution (and may modify the inputs), and `after_kickoff_callbacks` after execution (and may modify the `CrewOutput`).
  Source: [CrewAI Crews](https://docs.crewai.com/v1.15.22/en/concepts/crews.md) (overview, attributes, kickoff example).
  Recommended use: map XiaoPaw's per-message `_handle` to one Crew kickoff; use callbacks for request-path telemetry/guardrails at the documented lifecycle boundaries.

- **CrewAI tools are callable agent capabilities.** The tools documentation describes tools as functions agents can use for actions, with error handling for tool failures; asynchronous execution is available through `crew.kickoff_async()`.
  Source: [CrewAI Tools](https://docs.crewai.com/v1.15.22/en/concepts/tools.md) (overview, execution, async kickoff, error handling).
  Recommended use: treat each tool invocation as a downstream hop in the request path and preserve its failure/result boundary in logs and user-facing error handling.

- **MCP separates host, client, server, and transport.** An MCP host (the AI application) creates one MCP client per server; each client maintains a dedicated connection. MCP's data layer is JSON-RPC-based and includes discovery plus primitives such as tools, resources, prompts, and notifications; the transport layer handles connection, framing, and authorization. MCP specifies context exchange, not how an application uses an LLM.
  Source: [MCP Architecture overview](https://modelcontextprotocol.io/docs/learn/architecture) (participants and data/transport layers).
  Recommended use: in XiaoPaw traces, distinguish the Crew/agent host from the MCP client connection and remote/local MCP server; do not attribute server work to the Crew itself.

- **`asyncio.Queue` and Tasks make the ingress-to-worker handoff explicit.** Python documents asyncio queues as not thread-safe and intended for async code; bounded queues make `await put()` block once `maxsize` is reached. `asyncio.create_task()` schedules a coroutine and returns a Task, while tasks are awaitable.
  Sources: [asyncio queues](https://docs.python.org/3/library/asyncio-queue.html) and [asyncio tasks](https://docs.python.org/3/library/asyncio-task.html).
  Recommended use: read XiaoPaw's per-routing-key queue as the serialization/back-pressure boundary, and its worker Task as the owner of the request's sequential path through session, Crew, tool/MCP calls, reply, and persistence.

## Minimal path model

`inbound message → bounded per-routing-key asyncio.Queue → worker Task → Crew kickoff → tool/MCP hops → callbacks/telemetry → reply + persistence`

