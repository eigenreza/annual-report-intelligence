"""Query parsing and top-k retrieval over the persisted index.

Company names found in the question route retrieval to that company's chunks,
and multi-company questions are retrieved per company and merged so that one
company's larger report cannot crowd out another. Years are never used as a hard
filter: a figure for a year often lives in a later report's comparative columns
or multi-year overview, so years only add a small ranking bonus.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

import faiss
import numpy as np

from src.config import INDEX_DIR
from src.index.build_index import CHUNKS_FILE, INDEX_FILE
from src.ingest.chunk import Chunk
from src.ingest.registry import COMPANIES
from src.llm import LLM

DEFAULT_K = 8
MULTI_COMPANY_MIN_QUOTA = 4
YEAR_BONUS = 0.03
TABLE_BONUS = 0.02

YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
METRIC_WORDS = re.compile(
    r"\b(revenue|revenues|sales|profit|profits|income|earnings|ebit|ebitda|ebt|margin|growth|loss)\b",
    re.IGNORECASE,
)

# Retrieval vocabulary differs between US and European reports (net income versus
# net profit, revenue versus revenues), so metric words in the question are expanded
# with their common synonyms before embedding.
METRIC_EXPANSIONS = {
    "revenue": "revenues total revenues net sales",
    "sales": "revenues total revenues",
    "profit": "net income net profit profit before tax EBIT EBT earnings",
    "earnings": "net income net profit",
    "ebitda": "EBITDA EBIT depreciation and amortisation",
    "growth": "change in % compared with previous year increase",
    "margin": "margin return on sales net income margin",
}


@dataclass
class QueryInfo:
    text: str
    companies: list[str]
    years: list[int]
    expanded: str


@dataclass
class Hit:
    chunk: Chunk
    score: float


def parse_query(question: str) -> QueryInfo:
    lowered = question.lower()
    companies = [c for c in COMPANIES if re.search(rf"\b{re.escape(c.lower())}\b", lowered)]
    years = sorted({int(y) for y in YEAR_RE.findall(question)})
    extras = [phrase for word, phrase in METRIC_EXPANSIONS.items() if re.search(rf"\b{word}s?\b", lowered)]
    expanded = question if not extras else f"{question}\n{' '.join(extras)}"
    return QueryInfo(text=question, companies=companies, years=years, expanded=expanded)


class Retriever:
    def __init__(self, llm: LLM, index_dir: Path = INDEX_DIR) -> None:
        self.llm = llm
        self.index = faiss.read_index(str(index_dir / INDEX_FILE))
        data = json.loads((index_dir / CHUNKS_FILE).read_text(encoding="utf-8"))
        self.chunks = [Chunk.from_dict(item) for item in data]

    def _ranked(self, info: QueryInfo) -> list[Hit]:
        vector = self.llm.embed([info.expanded])
        scores, ids = self.index.search(vector, self.index.ntotal)
        wants_metric = bool(METRIC_WORDS.search(info.text))
        hits: list[Hit] = []
        for score, idx in zip(scores[0], ids[0]):
            if idx < 0:
                continue
            chunk = self.chunks[int(idx)]
            bonus = 0.0
            if info.years and any(str(y) in chunk.text for y in info.years):
                bonus += YEAR_BONUS
            if wants_metric and chunk.chunk_type == "table":
                bonus += TABLE_BONUS
            hits.append(Hit(chunk, float(score) + bonus))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits

    def search(self, question: str, k: int = DEFAULT_K) -> list[Hit]:
        info = parse_query(question)
        ranked = self._ranked(info)
        if not info.companies:
            return ranked[:k]
        if len(info.companies) == 1:
            quota = k
        else:
            quota = max(MULTI_COMPANY_MIN_QUOTA, math.ceil(k / len(info.companies)) + 1)
        merged: list[Hit] = []
        for company in info.companies:
            merged.extend([h for h in ranked if h.chunk.company == company][:quota])
        merged.sort(key=lambda h: h.score, reverse=True)
        return merged
