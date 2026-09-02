"""Prompt assembly and citation handling for the answering model."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.index.retrieve import Hit
from src.ingest.registry import REGISTRY
from src.llm import LLM

SYSTEM_PROMPT = """You are a research assistant for a financial analyst. You answer questions about annual reports strictly from the document excerpts supplied with each question.

Rules
1. Use only the supplied excerpts. Every figure you state must appear in an excerpt. If the excerpts do not contain the requested figure, say so plainly and then give the closest related figures that the excerpts do contain. Never fill a gap from general knowledge, even when you believe you know the real figure.
2. Cite the source of every figure in parentheses as (file name, page N), for example (Tesla_Annual_Report_2023.pdf, page 20). For excerpts located by paragraph, cite (file name, paragraphs A-B). Take the file name and location from the header line of the excerpt.
3. Respect the reporting entity named in each excerpt header. Figures reported by a subsidiary belong to that subsidiary, not to the parent group. BMW Finance N.V. is a financing subsidiary; its interest income and net result are not BMW Group revenue or profit and must never be presented as such. When a question about a company touches a year for which the corpus only holds a subsidiary's report, explain the distinction, give the subsidiary's figures clearly labelled if they help, and state that the group figures for that year are not in the provided documents.
4. State currencies and units explicitly, for example EUR million or USD million. When comparing companies that report in different currencies, present each figure with its own currency, or compare margins, and say which you are doing. Never convert or silently mix currencies.
5. When a question is ambiguous about the metric (profit can mean net income, EBIT or EBT), answer with net income as the primary reading and mention the other measures if they are in the excerpts.
6. Annual reports carry comparative figures for earlier years and multi-year overviews, so a figure for a year may appear in a later report. Use such figures when present and cite where they appear.
7. Be concise and precise. Use a short table when several figures are presented. Do not speculate about causes or figures that the excerpts do not support.

Documents in the corpus
{overview}"""

CITATION_RE = re.compile(
    r"\(\s*([A-Za-z0-9_\-]+\.(?:pdf|docx))\s*,\s*(?:pages?|p\.|pp\.|paragraphs?|para\.)\s*([\d\s,\-–]+)\s*\)",
    re.IGNORECASE,
)


def corpus_overview() -> str:
    lines = []
    for info in REGISTRY:
        line = f"- {info.label}"
        if info.company and info.note:
            line += f". {info.note}"
        lines.append(line)
    return "\n".join(lines)


def build_prompt(question: str, hits: list[Hit]) -> tuple[str, str]:
    system = SYSTEM_PROMPT.format(overview=corpus_overview())
    excerpts = "\n\n".join(f"{h.chunk.header}\n{h.chunk.text}" for h in hits)
    user = f"Question: {question}\n\nExcerpts:\n\n{excerpts}"
    return system, user


def _numbers(span: str) -> set[int]:
    values: set[int] = set()
    for part in re.split(r"[,\s]+", span.strip()):
        if not part:
            continue
        if re.fullmatch(r"\d+[\-–]\d+", part):
            a, b = re.split(r"[\-–]", part)
            values.update(range(int(a), int(b) + 1))
        elif part.isdigit():
            values.add(int(part))
    return values


def _locator_numbers(locator: str) -> set[int]:
    return _numbers(locator.split(" ", 1)[1]) if " " in locator else set()


def extract_citations(text: str) -> list[tuple[str, set[int]]]:
    return [(file, _numbers(span)) for file, span in CITATION_RE.findall(text)]


def cited_hits(text: str, hits: list[Hit]) -> list[Hit]:
    """Retrieved chunks whose file and page (or paragraph range) the answer cites."""
    citations = extract_citations(text)
    cited: list[Hit] = []
    for hit in hits:
        pages = _locator_numbers(hit.chunk.locator)
        for file, numbers in citations:
            if file.lower() == hit.chunk.source_file.lower() and pages & numbers:
                cited.append(hit)
                break
    return cited


@dataclass
class Answer:
    text: str
    hits: list[Hit]
    cited: list[Hit] = field(default_factory=list)

    @property
    def has_citations(self) -> bool:
        return bool(extract_citations(self.text))

    def sources(self) -> list[str]:
        """One line per distinct cited location, or every retrieved location when
        the answer cites nothing, so the reader can always see what it rests on."""
        pool = self.cited or self.hits
        seen: list[str] = []
        for hit in pool:
            line = f"{hit.chunk.source_file}, {hit.chunk.locator} ({hit.chunk.entity}, {hit.chunk.chunk_type})"
            if line not in seen:
                seen.append(line)
        return seen


def answer_question(llm: LLM, question: str, hits: list[Hit]) -> Answer:
    system, user = build_prompt(question, hits)
    text = llm.complete(system, user)
    return Answer(text=text, hits=hits, cited=cited_hits(text, hits))
