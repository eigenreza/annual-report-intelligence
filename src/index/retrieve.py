"""Query parsing and top-k retrieval over the persisted index.

Company names found in the question route retrieval to that company's chunks,
and multi-company questions are retrieved per company and merged so that one
company's larger report cannot crowd out another. Years are never used as a hard
filter: a figure for a year often lives in a later report's comparative columns
or multi-year overview.

Ranking fuses two orderings by reciprocal rank: dense similarity of the embedded
question, and BM25 over the same chunks. Dense similarity alone cannot tell the
table rows of a financial report apart. For "Ford's revenue in 2020" it scores a
receivables row above the revenue row, because both are short lines of dollar
figures under the same header, and the word that decides the question carries no
weight. The lexical score supplies exactly that weight.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import faiss
import numpy as np

from src.config import INDEX_DIR
from src.index.build_index import CHUNKS_FILE, INDEX_FILE
from src.ingest.chunk import Chunk
from src.ingest.registry import COMPANIES, REGISTRY
from src.llm import LLM

DEFAULT_K = 12
MULTI_COMPANY_MIN_QUOTA = 6
RRF_K = 60
TOKEN_PREFIX = 6

YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
TOKEN_RE = re.compile(r"[a-z0-9]+")
# A question about "the companies" or "which company" without naming one is about
# all of them, and is retrieved per company like any other multi-company question.
COMPANY_WORDS = re.compile(r"\b(?:compan(?:y|ies)|automakers?|manufacturers?|carmakers?)\b", re.IGNORECASE)
# A question about the present state of something is answered from the most recent
# report of each named company, so that an older report's status list cannot be
# merged into the answer. Naming a year switches this off.
CURRENT_WORDS = re.compile(r"\b(?:currently|current|now|latest|most recent|at present|today)\b", re.IGNORECASE)

STOPWORDS = frozenset(
    """a an and are as at be between by can could did do does for from give how in is it its many
    much of on or over past please provide provided summary that the their these this those three to
    was were what which who with year years figure figures number numbers company companies overall
    recorded achieved currently stage""".split()
)

# Retrieval vocabulary differs between US and European reports (net income versus
# net profit, revenue versus revenues), so metric words in the question are expanded
# with their common synonyms: phrases for the embedded query, and only the
# distinctive terms for lexical matching, since "total", "before" or "tax" would
# match half of every report. Metric words are matched by prefix, so "profits" and
# "profitability" count as "profit". "Sales" is deliberately not among the revenue
# synonyms: in these reports it means vehicle units, not money.
METRIC_EXPANSIONS = {
    "revenue": "revenues total revenues",
    "profit": "net income net profit profit before tax EBIT EBT earnings",
    "earning": "net income net profit",
    "ebitda": "EBITDA EBIT depreciation and amortisation",
    "margin": "margin return on sales net income margin",
}
LEXICAL_SYNONYMS = {
    "revenue": "revenues",
    "profit": "income earnings EBIT EBT",
    "earning": "income profit",
    "ebitda": "EBIT",
    "margin": "",
}
# A question about a company's metric that names no segment or region is about the
# company as a whole. Regional and segment tables carry the same metric words as
# the consolidated table and outnumber it, so for such questions tables whose
# title names a segment or region are demoted rather than allowed to fill the
# quota. The same words in the question switch the demotion off.
SEGMENT_WORDS = re.compile(
    r"\b(?:automotive|credit|segment|segments|region|regional|europe|china|taiwan|america|international|"
    r"energy|services|motorcycles|division|brand|model e|ford blue|ford pro|ford next)\b",
    re.IGNORECASE,
)
SEGMENT_DEMOTION = 0.5
# Reports repeat the same table for several regions and years. Capping how many
# tables with the same title one company contributes keeps the context varied.
MAX_TABLES_PER_TITLE = 2
# The row whose label names the metric ("Revenue ($M)", "Net income", "Group
# revenues") is the row the question is about. A metric word anywhere in a chunk
# is weak evidence; in the label it is close to decisive, and it gets a bonus on
# the scale of the top fused scores.
LABEL_BONUS = 0.02


@dataclass
class QueryInfo:
    text: str
    companies: list[str]
    years: list[int]
    expanded: str
    metrics: list[str]
    lexical: list[str]
    latest_only: bool = False
    company_level: bool = False


@dataclass
class Hit:
    chunk: Chunk
    score: float


def tokenize(text: str) -> list[str]:
    """Lower-cased alphanumeric tokens cut to a prefix, so that revenue and revenues,
    or profit and profitability, count as the same term."""
    return [t[:TOKEN_PREFIX] for t in TOKEN_RE.findall(text.lower()) if len(t) > 1]


def query_tokens(text: str) -> list[str]:
    """Question tokens with function words removed, judged on the full word before
    it is cut to its prefix."""
    return [t[:TOKEN_PREFIX] for t in TOKEN_RE.findall(text.lower()) if len(t) > 1 and t not in STOPWORDS]


def parse_query(question: str) -> QueryInfo:
    lowered = question.lower()
    companies = [c for c in COMPANIES if re.search(rf"\b{re.escape(c.lower())}\b", lowered)]
    if not companies and COMPANY_WORDS.search(question):
        companies = list(COMPANIES)
    years = sorted({int(y) for y in YEAR_RE.findall(question)})
    metrics = [word for word in METRIC_EXPANSIONS if re.search(rf"\b{word}", lowered)]
    extras = [METRIC_EXPANSIONS[word] for word in metrics]
    expanded = question if not extras else f"{question}\n{' '.join(extras)}"
    lexical = query_tokens(question) + [t for word in metrics for t in tokenize(LEXICAL_SYNONYMS[word])]
    return QueryInfo(
        text=question,
        companies=companies,
        years=years,
        expanded=expanded,
        metrics=metrics,
        lexical=lexical,
        latest_only=bool(CURRENT_WORDS.search(question)) and not years,
        company_level=bool(metrics) and not SEGMENT_WORDS.search(question),
    )


def alias_phrases(metrics: list[str]) -> dict[str, list[set[str]]]:
    """Token sets of the phrases each reporting entity uses for the question's
    metrics, keyed by entity. A row label matches an alias only when it contains
    the whole phrase, so "interest income" does not light up every line of a
    financing subsidiary's report that mentions interest."""
    phrases: dict[str, list[set[str]]] = {}
    for info in REGISTRY:
        for word, phrase in info.aliases:
            tokens = set(tokenize(phrase))
            if word in metrics and tokens not in phrases.setdefault(info.entity, []):
                phrases[info.entity].append(tokens)
    return {entity: found for entity, found in phrases.items() if found}


def metric_terms(info: QueryInfo) -> set[str]:
    """Prefix tokens that name the question's metrics, including synonyms."""
    words = " ".join(info.metrics) + " " + " ".join(LEXICAL_SYNONYMS[m] for m in info.metrics)
    return set(tokenize(words))


def row_label_tokens(chunk: Chunk) -> set[str]:
    return set(tokenize(chunk.text.split(":", 1)[0])) if chunk.chunk_type == "row" else set()


def table_title(chunk: Chunk) -> str:
    return chunk.text.split("\n", 1)[0] if chunk.chunk_type == "table" else ""


def demote_segment_tables(hits: list[Hit]) -> list[Hit]:
    """Halve the score of tables whose title names a segment or region."""
    adjusted = [
        Hit(h.chunk, h.score * SEGMENT_DEMOTION) if SEGMENT_WORDS.search(table_title(h.chunk)) else h for h in hits
    ]
    return sorted(adjusted, key=lambda h: h.score, reverse=True)


def diversify(hits: list[Hit], limit: int, per_title: int = MAX_TABLES_PER_TITLE) -> list[Hit]:
    """Take the top hits while allowing at most per_title tables with the same title."""
    chosen: list[Hit] = []
    seen: Counter = Counter()
    for hit in hits:
        title = table_title(hit.chunk)
        if title:
            if seen[title] >= per_title:
                continue
            seen[title] += 1
        chosen.append(hit)
        if len(chosen) == limit:
            break
    return chosen


class BM25:
    """Okapi BM25 over tokenized documents, enough for a few thousand chunks."""

    def __init__(self, documents: list[list[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.size = len(documents)
        self.lengths = np.array([len(d) for d in documents], dtype="float32")
        self.average = float(self.lengths.mean()) if self.size else 0.0
        self.postings: dict[str, dict[int, int]] = defaultdict(dict)
        for index, document in enumerate(documents):
            for term, count in Counter(document).items():
                self.postings[term][index] = count

    def scores(self, terms: list[str]) -> np.ndarray:
        result = np.zeros(self.size, dtype="float32")
        for term in set(terms):
            posting = self.postings.get(term)
            if not posting:
                continue
            frequency = len(posting)
            idf = math.log(1 + (self.size - frequency + 0.5) / (frequency + 0.5))
            for index, count in posting.items():
                norm = self.k1 * (1 - self.b + self.b * self.lengths[index] / self.average)
                result[index] += idf * count * (self.k1 + 1) / (count + norm)
        return result


def reciprocal_rank_fusion(*rankings: list[int], size: int, k: int = RRF_K) -> np.ndarray:
    """Fuse orderings of document indices. A document absent from an ordering gets
    the rank just past its end, so a zero lexical score still contributes a little."""
    fused = np.zeros(size, dtype="float32")
    for ranking in rankings:
        fused += 1.0 / (k + len(ranking) + 1)
        for rank, index in enumerate(ranking, start=1):
            fused[index] += 1.0 / (k + rank) - 1.0 / (k + len(ranking) + 1)
    return fused


def collapse_to_parents(scored: list[tuple[Chunk, float]], by_id: dict[str, Chunk]) -> list[Hit]:
    """Replace row hits by their table and keep the best score per chunk."""
    best: dict[str, Hit] = {}
    for chunk, score in scored:
        target = by_id[chunk.parent_id] if chunk.parent_id else chunk
        if target.chunk_id not in best or score > best[target.chunk_id].score:
            best[target.chunk_id] = Hit(target, score)
    return sorted(best.values(), key=lambda h: h.score, reverse=True)


def restrict_to_latest(hits: list[Hit], latest_year: dict[str, int]) -> list[Hit]:
    """Keep only chunks from each company's most recent report."""
    return [h for h in hits if h.chunk.company is None or h.chunk.year == latest_year.get(h.chunk.company)]


class Retriever:
    def __init__(self, llm: LLM, index_dir: Path = INDEX_DIR) -> None:
        self.llm = llm
        self.index = faiss.read_index(str(index_dir / INDEX_FILE))
        data = json.loads((index_dir / CHUNKS_FILE).read_text(encoding="utf-8"))
        self.chunks = [Chunk.from_dict(item) for item in data]
        self.by_id = {c.chunk_id: c for c in self.chunks}
        self.bm25 = BM25([tokenize(c.embed_text) for c in self.chunks])
        self.latest_year: dict[str, int] = {}
        for chunk in self.chunks:
            if chunk.company and chunk.year:
                self.latest_year[chunk.company] = max(chunk.year, self.latest_year.get(chunk.company, 0))

    @property
    def readable_chunks(self) -> list[Chunk]:
        """Chunks the model can be shown: everything except row children."""
        return [c for c in self.chunks if c.parent_id is None]

    def _dense_order(self, info: QueryInfo) -> list[int]:
        vector = self.llm.embed([info.expanded])
        _scores, ids = self.index.search(vector, self.index.ntotal)
        return [int(i) for i in ids[0] if i >= 0]

    def _ranked(self, info: QueryInfo, dense_order: list[int]) -> list[Hit]:
        lexical = self.bm25.scores(info.lexical)
        lexical_order = [int(i) for i in np.argsort(-lexical) if lexical[i] > 0]
        fused = reciprocal_rank_fusion(dense_order, lexical_order, size=len(self.chunks))
        terms = metric_terms(info)
        aliases = alias_phrases(info.metrics)
        scored: list[tuple[Chunk, float]] = []
        for i, chunk in enumerate(self.chunks):
            score = float(fused[i])
            if terms:
                labels = row_label_tokens(chunk)
                if labels & terms or any(phrase <= labels for phrase in aliases.get(chunk.entity, [])):
                    score += LABEL_BONUS
            scored.append((chunk, score))
        hits = collapse_to_parents(scored, self.by_id)
        if info.company_level:
            hits = demote_segment_tables(hits)
        if info.latest_only and info.companies:
            hits = restrict_to_latest(hits, self.latest_year)
        return hits

    def search(self, question: str, k: int = DEFAULT_K) -> list[Hit]:
        info = parse_query(question)
        ranked = self._ranked(info, self._dense_order(info))
        if not info.companies:
            return diversify(ranked, k)
        if len(info.companies) == 1:
            quota = k
        else:
            quota = max(MULTI_COMPANY_MIN_QUOTA, math.ceil(k / len(info.companies)) + 1)
        merged: list[Hit] = []
        for company in info.companies:
            merged.extend(diversify([h for h in ranked if h.chunk.company == company], quota))
        merged.sort(key=lambda h: h.score, reverse=True)
        return merged
