"""Thin wrapper around the OpenAI client for chat completions and embeddings."""

from __future__ import annotations

import numpy as np
from openai import OpenAI

from src.config import EMBEDDING_MODEL, chat_model, require_api_key

# Reasoning models reject an explicit temperature; every other model gets a fixed 0.
_NO_TEMPERATURE_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def accepts_temperature(model: str) -> bool:
    return not model.startswith(_NO_TEMPERATURE_PREFIXES)


class LLM:
    def __init__(self, model: str | None = None) -> None:
        require_api_key()
        self.client = OpenAI()
        self.model = model or chat_model()

    def complete(self, system: str, user: str) -> str:
        kwargs = {"temperature": 0.0} if accepts_temperature(self.model) else {}
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            **kwargs,
        )
        return (response.choices[0].message.content or "").strip()

    def embed(self, texts: list[str], batch_size: int = 100) -> np.ndarray:
        """Unit-normalised embeddings, one row per input text, as float32."""
        vectors: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            response = self.client.embeddings.create(model=EMBEDDING_MODEL, input=batch)
            vectors.extend(item.embedding for item in response.data)
        matrix = np.asarray(vectors, dtype="float32")
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        return matrix / np.maximum(norms, 1e-12)
