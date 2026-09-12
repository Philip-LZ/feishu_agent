"""运行时环境变量配置。"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv as _load_dotenv


def load_dotenv(path: Path) -> bool:
    """读取指定 .env 文件，且不覆盖已由外部环境设置的变量。"""
    return _load_dotenv(dotenv_path=path, override=False)
