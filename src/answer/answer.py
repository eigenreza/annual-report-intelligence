"""Prompt assembly and citation handling for the answering model."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.index.retrieve import Hit
from src.ingest.registry import REGISTRY
from src.llm import LLM

SYSTEM_PROMPT = """You are a research assistant for a financial analyst. You answer questions about annual reports strictly from the document excerpts supplied with each question.

Rules
1. Use only the supplied excerpts. Every figure you state must appear in an excerpt. If the excerpts do not contain the requested figure, say so plainly, name the document the corpus holds for that company and year (from the list below) and what it covers, and give the closest related figures that the excerpts do contain. Never fill a gap from general knowledge, even when you believe you know the real figure.
2. Cite the source of every figure in parentheses directly after the figure or the sentence that contains it, in exactly this form: (file name, page N), for example (Tesla_Annual_Report_2023.pdf, page 20). For excerpts located by paragraph use (file name, paragraphs A-B). Take the file name and location from the header line of the excerpt. Do not describe the source in prose instead of citing it.
3. Respect the reporting entity named in each excerpt header. Figures reported by a subsidiary belong to that subsidiary, not to the parent group. BMW Finance N.V. is a financing subsidiary; its interest income and net result are not BMW Group revenue or profit and must never be presented as such. Whenever a question asks for or implies a company's figures for a year in which the corpus only holds a subsidiary's report, say so by name, state that the group figures for that year are not in the provided documents, and give the subsidiary's own corresponding figures for that year from the excerpts (for example its interest income and net result), clearly labelled as the subsidiary's.
4. State currencies and units explicitly, for example EUR million or USD million. When comparing companies that report in different currencies, present each figure with its own currency, or compare margins, and say which you are doing. Never convert or silently mix currencies.
5. When a question is ambiguous about the metric (profit can mean net income, EBIT or EBT), answer with net income as the primary reading and mention the other measures if they are in the excerpts. Prefer consolidated company totals over segment or sub-line figures; if only a segment figure is available, label it as such.
6. Annual reports carry comparative figures for earlier years and multi-year overviews, so a figure for a year may appear in a later report. Use such figures when present and cite where they appear.
7. When a question asks about the current state of something (currently, now, latest), answer from the most recent report in the corpus and name its year. Do not merge statuses or lists from reports of different years; an older report's version may be mentioned separately as history.
8. Answer the question that was asked. Do not pad the answer with figures or metrics the question did not ask for. Be concise and precise, use a short table when several figures are presented, and do not speculate about causes or figures that the excerpts do not support.

Documents in the corpus
{overview}"""

# A citation opens with "(" or, inside a shared parenthesis or a "Sources:" line,
# follows ";", ":" or "and". It names a file, then a page or paragraph locator such
# as "page 20", "pp. 10-11" or "pages 10 and 11", and ends before ")", ";", a line
# end, or an "and" that introduces the next citation.
CITATION_RE = re.compile(
    r"(?:[(;:]|\band)\s*([A-Za-z0-9_\-]+\.(?:pdf|docx))\s*,?\s*(?:pages?|pp?\.|paragraphs?|para\.)\s*"
    r"((?:\d+|and|pages?|pp?\.|[\s,\-–])+?)\s*(?=[;)]|\.?\s*$|\s+and\s+[A-Za-z0-9_\-]+\.(?:pdf|docx)|\.\s)",
    re.IGNORECASE | re.MULTILINE,
)
# The prose form "page 10 of BMW_Annual_Report_2021.pdf" is accepted as well, so a
# source named that way still counts as cited.
PROSE_CITATION_RE = re.compile(
    r"(?:pages?|pp?\.|paragraphs?|para\.)\s*((?:\d+|and|[\s,\-–])+?)\s+(?:of|in)\s+([A-Za-z0-9_\-]+\.(?:pdf|docx))",
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
    found = [(m.start(), m.group(1), m.group(2)) for m in CITATION_RE.finditer(text)]
    found += [(m.start(), m.group(2), m.group(1)) for m in PROSE_CITATION_RE.finditer(text)]
    return [(file, _numbers(span)) for _, file, span in sorted(found)]


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
