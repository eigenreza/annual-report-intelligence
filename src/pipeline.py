"""The question-answering pipeline: rewrite, retrieve, answer, remember."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.answer.answer import Answer, answer_question
from src.answer.rewrite import rewrite_question
from src.config import DATA_DIR, INDEX_DIR
from src.index.build_index import ensure_index
from src.index.retrieve import DEFAULT_K, Retriever
from src.llm import LLM


@dataclass
class Turn:
    question: str
    standalone: str
    answer: Answer

    @property
    def was_rewritten(self) -> bool:
        return self.standalone.strip().lower() != self.question.strip().lower()


class Pipeline:
    def __init__(
        self,
        llm: LLM | None = None,
        k: int = DEFAULT_K,
        data_dir: Path = DATA_DIR,
        index_dir: Path = INDEX_DIR,
        progress: Callable[[str], None] = print,
    ) -> None:
        self.llm = llm or LLM()
        self.k = k
        ensure_index(self.llm, data_dir, index_dir, progress)
        self.retriever = Retriever(self.llm, index_dir)
        self.history: list[Turn] = []

    def ask(self, question: str) -> Turn:
        pairs = [(t.question, t.answer.text) for t in self.history]
        standalone = rewrite_question(self.llm.complete, question, pairs)
        hits = self.retriever.search(standalone, self.k)
        answer = answer_question(self.llm, standalone, hits)
        turn = Turn(question=question, standalone=standalone, answer=answer)
        self.history.append(turn)
        return turn

    def reset(self) -> None:
        self.history.clear()

    @property
    def last(self) -> Turn | None:
        return self.history[-1] if self.history else None
