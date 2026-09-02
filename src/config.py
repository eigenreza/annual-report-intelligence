"""Paths and settings shared across the package."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
INDEX_DIR = ROOT / "index_store"

load_dotenv(ROOT / ".env")

EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_CHAT_MODEL = "gpt-4.1-mini"


def chat_model() -> str:
    """The chat model from .env, or the default when the variable is unset or empty."""
    return os.environ.get("OPENAI_MODEL", "").strip() or DEFAULT_CHAT_MODEL


def require_api_key() -> None:
    """Fail early with a plain message when the key is missing. The value itself is never printed."""
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Add your OpenAI API key to the .env file in the project root."
        )
