"""A completed turn schedules one background memory write."""

from unittest.mock import AsyncMock

import pytest

from xiaopaw.models import InboundMessage
from xiaopaw.runner import Runner
from xiaopaw.session.models import SessionEntry


@pytest.mark.asyncio
async def test_runner_indexes_completed_turn(monkeypatch, tmp_path):
    indexed = AsyncMock()
    monkeypatch.setattr("xiaopaw.runner.async_index_turn", indexed)
    session_mgr = AsyncMock()
    session_mgr.get_or_create.return_value = SessionEntry(id="session-1")
    session_mgr.load_history.return_value = []
    sender = AsyncMock()
    sender.send_thinking.return_value = None
    runner = Runner(
        session_mgr=session_mgr,
        sender=sender,
        agent_fn=AsyncMock(return_value="reply"),
        data_dir=tmp_path,
        db_dsn="postgresql://example/db",
    )

    await runner._handle(InboundMessage(routing_key="p2p:user-1", content="hello", msg_id="m1"))
    await runner.shutdown()

    indexed.assert_awaited_once()
    assert indexed.await_args.kwargs["session_id"] == "session-1"
    assert indexed.await_args.kwargs["routing_key"] == "p2p:user-1"
    assert indexed.await_args.kwargs["user_message"] == "hello"
    assert indexed.await_args.kwargs["assistant_reply"] == "reply"
