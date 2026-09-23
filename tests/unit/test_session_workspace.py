from pathlib import Path

import pytest

from xiaopaw.session.manager import SessionManager


@pytest.mark.asyncio
async def test_session_workspace_directories_are_created_and_restored(tmp_path: Path):
    workspace = tmp_path / "workspace"
    manager = SessionManager(tmp_path / "data", workspace_dir=workspace)

    session = await manager.create_new_session("p2p:user")
    session_dir = workspace / "sessions" / session.id
    assert {p.name for p in session_dir.iterdir()} == {"uploads", "outputs", "tmp"}

    (session_dir / "outputs").rmdir()
    manager = SessionManager(tmp_path / "data", workspace_dir=workspace)
    assert (session_dir / "outputs").is_dir()

    (session_dir / "outputs").rmdir()
    assert (await manager.get_or_create("p2p:user")).id == session.id
    assert (session_dir / "outputs").is_dir()
