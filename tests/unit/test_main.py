"""main 模块配置加载测试。"""

from __future__ import annotations

import os
from pathlib import Path

from xiaopaw.env import load_dotenv


class TestLoadDotenv:
    def test_loads_qwen_api_key_from_dotenv(self, tmp_path: Path, monkeypatch):
        """.env 中的 QWEN_API_KEY 应在进程启动时可用。"""
        monkeypatch.delenv("QWEN_API_KEY", raising=False)
        dotenv_path = tmp_path / ".env"
        dotenv_path.write_text("QWEN_API_KEY=dotenv-key\n", encoding="utf-8")

        load_dotenv(dotenv_path)

        assert os.environ["QWEN_API_KEY"] == "dotenv-key"

    def test_keeps_exported_value_when_dotenv_has_same_key(
        self, tmp_path: Path, monkeypatch
    ):
        """已 export 的变量必须优先于 .env 中的同名配置。"""
        monkeypatch.setenv("QWEN_API_KEY", "exported-key")
        dotenv_path = tmp_path / ".env"
        dotenv_path.write_text("QWEN_API_KEY=dotenv-key\n", encoding="utf-8")

        load_dotenv(dotenv_path)

        assert os.environ["QWEN_API_KEY"] == "exported-key"
