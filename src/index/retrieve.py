"""Query parsing and top-k retrieval over the persisted index.

Company names found in the question route retrieval to that company's chunks,
and multi-company questions are retrieved per company and merged so that one
company's larger report cannot crowd out another. Years are never used as a hard
filter: a figure for a year often lives in a later report's comparative columns
or multi-year overview.

Ranking is dense similarity plus a small bonus for the share of the question's
content words that appear in the chunk. Within one report many chunks embed
almost identically for a short question, and the word that separates a key
metrics table from a balance sheet is often just "revenue".
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
MULTI_COMPANY_MIN_QUOTA = 5
KEYWORD_BONUS = 0.08
KEYWORD_PREFIX = 6
TABLE_BONUS = 0.02

STOPWORDS = frozenset(
    """a an and are as at be between by can could did do does for from give how in is it its many
    much of on or over past please provide provided summary that the their these this those three to
    was were what which who with year years figure figures number numbers company companies overall
    recorded achieved currently stage""".split()
)

YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
# A question about "the companies" or "which company" without naming one is about
# all of them, and is retrieved per company like any other multi-company question.
COMPANY_WORDS = re.compile(r"\b(?:compan(?:y|ies)|automakers?|manufacturers?|carmakers?)\b", re.IGNORECASE)
# A question about the present state of something is answered from the most recent
# report of each named company, so that an older report's status list cannot be
# merged into the answer. Naming a year switches this off.
CURRENT_WORDS = re.compile(r"\b(?:currently|current|now|latest|most recent|at present|today)\b", re.IGNORECASE)
METRIC_WORDS = re.compile(
    r"\b(revenue|revenues|sales|profit|profits|income|earnings|ebit|ebitda|ebt|margin|growth|loss)\b",
    re.IGNORECASE,
)

# Retrieval vocabulary differs between US and European reports (net income versus
# net profit, revenue versus revenues), so metric words in the question are expanded
# with their common synonyms before embedding. "Sales" is deliberately not among the
# revenue synonyms: in these reports it means vehicle units, not money.
METRIC_EXPANSIONS = {
    "revenue": "revenues total revenues",
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
    keywords: set[str]
    latest_only: bool = False


@dataclass
class Hit:
    chunk: Chunk
    score: float


def keywords(question: str) -> set[str]:
    """Content words of the question, cut to a prefix so that revenue also matches
    revenues and profitability also matches profit."""
    words = re.findall(r"[a-z0-9]+", question.lower())
    return {w[:KEYWORD_PREFIX] for w in words if len(w) > 2 and w not in STOPWORDS}


def keyword_overlap(keys: set[str], text: str) -> float:
    if not keys:
        return 0.0
    lowered = text.lower()
    hits = sum(1 for key in keys if re.search(rf"\b{re.escape(key)}", lowered))
    return hits / len(keys)


def parse_query(question: str) -> QueryInfo:
    lowered = question.lower()
    companies = [c for c in COMPANIES if re.search(rf"\b{re.escape(c.lower())}\b", lowered)]
    if not companies and COMPANY_WORDS.search(question):
        companies = list(COMPANIES)
    years = sorted({int(y) for y in YEAR_RE.findall(question)})
    extras = [phrase for word, phrase in METRIC_EXPANSIONS.items() if re.search(rf"\b{word}s?\b", lowered)]
    expanded = question if not extras else f"{question}\n{' '.join(extras)}"
    return QueryInfo(
        text=question,
        companies=companies,
        years=years,
        expanded=expanded,
        keywords=keywords(question),
        latest_only=bool(CURRENT_WORDS.search(question)) and not years,
    )


def restrict_to_latest(hits: list[Hit], latest_year: dict[str, int]) -> list[Hit]:
    """Keep only chunks from each company's most recent report."""
    return [h for h in hits if h.chunk.company is None or h.chunk.year == latest_year.get(h.chunk.company)]


def collapse_to_parents(scored: list[tuple[Chunk, float]], by_id: dict[str, Chunk]) -> list[Hit]:
    """Replace row hits by their table and keep the best score per chunk."""
    best: dict[str, Hit] = {}
    for chunk, score in scored:
        target = by_id[chunk.parent_id] if chunk.parent_id else chunk
        if target.chunk_id not in best or score > best[target.chunk_id].score:
            best[target.chunk_id] = Hit(target, score)
    return sorted(best.values(), key=lambda h: h.score, reverse=True)


class Retriever:
    def __init__(self, llm: LLM, index_dir: Path = INDEX_DIR) -> None:
        self.llm = llm
        self.index = faiss.read_index(str(index_dir / INDEX_FILE))
        data = json.loads((index_dir / CHUNKS_FILE).read_text(encoding="utf-8"))
        self.chunks = [Chunk.from_dict(item) for item in data]
        self.by_id = {c.chunk_id: c for c in self.chunks}
        self.latest_year: dict[str, int] = {}
        for chunk in self.chunks:
            if chunk.company and chunk.year:
                self.latest_year[chunk.company] = max(chunk.year, self.latest_year.get(chunk.company, 0))

    @property
    def readable_chunks(self) -> list[Chunk]:
        """Chunks the model can be shown: everything except row children."""
        return [c for c in self.chunks if c.parent_id is None]

    def _ranked(self, info: QueryInfo) -> list[Hit]:
        vector = self.llm.embed([info.expanded])
        scores, ids = self.index.search(vector, self.index.ntotal)
        wants_metric = bool(METRIC_WORDS.search(info.text))
        scored: list[tuple[Chunk, float]] = []
        for score, idx in zip(scores[0], ids[0]):
            if idx < 0:
                continue
            chunk = self.chunks[int(idx)]
            bonus = KEYWORD_BONUS * keyword_overlap(info.keywords, chunk.embed_text)
            if wants_metric and chunk.chunk_type in ("table", "row"):
                bonus += TABLE_BONUS
            scored.append((chunk, float(score) + bonus))
        return collapse_to_parents(scored, self.by_id)

    def search(self, question: str, k: int = DEFAULT_K) -> list[Hit]:
        info = parse_query(question)
        ranked = self._ranked(info)
        if info.latest_only and info.companies:
            ranked = restrict_to_latest(ranked, self.latest_year)
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
