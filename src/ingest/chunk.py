"""Structure-aware chunking.

Tables stay whole, or are split by row groups with the header repeated. Running
text is cut into windows of a few hundred tokens at line boundaries, preferring
sentence and heading ends, with a small overlap. Every chunk carries the registry
metadata of its document and its location in that document.

Each table row is also emitted as a small child chunk that points at its table.
A table that mixes a dozen metrics embeds as a blur, so a question about one line
of it ranks the whole table below smaller, more specific chunks. The row chunks
are what retrieval matches against; the table they belong to is what the model
gets to read.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import tiktoken

from src.ingest.parse import Unit
from src.ingest.registry import DocumentInfo

TARGET_TOKENS = 400
MAX_TOKENS = 520
OVERLAP_LINES = 2

_encoding = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_encoding.encode(text))


@dataclass
class Chunk:
    chunk_id: str
    text: str
    embed_text: str
    company: str | None
    year: int | None
    entity: str
    currency: str | None
    source_file: str
    page: int | None
    locator: str
    chunk_type: str
    parent_id: str | None = None

    @property
    def header(self) -> str:
        return f"[{self.source_file} | {self.entity} | {self.locator}]"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Chunk":
        return cls(**data)


def _embed_header(info: DocumentInfo, locator: str) -> str:
    """Context prepended to the embedded text so that company, entity and year are
    part of what the embedding sees, not only the page content."""
    if info.company:
        return f"{info.entity} annual report {info.year} ({info.company}, figures in {info.currency}), {locator}"
    return f"{info.entity}: {info.note}, {locator}"


def _is_boundary(line: str) -> bool:
    stripped = line.rstrip()
    return stripped.endswith((".", ":", "?", "!")) or (len(stripped) < 60 and not stripped.endswith(","))


def split_lines(
    lines: list[str],
    target: int = TARGET_TOKENS,
    limit: int = MAX_TOKENS,
    overlap: int = OVERLAP_LINES,
) -> list[list[str]]:
    """Group lines into windows of roughly target tokens, never above limit, cutting
    at a boundary line where possible and repeating the last lines of a window at
    the start of the next."""
    windows: list[list[str]] = []
    current: list[str] = []
    size = 0
    for line in lines:
        cost = count_tokens(line) + 1
        if current and (size + cost > limit or (size >= target and _is_boundary(current[-1]))):
            windows.append(current)
            carried = current[-overlap:] if overlap else []
            current = list(carried)
            size = sum(count_tokens(l) + 1 for l in current)
        current.append(line)
        size += cost
    if current and (not windows or len(current) > overlap):
        windows.append(current)
    return windows


def _table_chunks(unit: Unit, limit: int) -> list[str]:
    header = unit.table_header
    rows = unit.text[len(header) :].strip("\n").splitlines() if header else unit.text.splitlines()
    if count_tokens(unit.text) <= limit:
        return [unit.text]
    budget = limit - count_tokens(header) - 1
    parts: list[str] = []
    group: list[str] = []
    size = 0
    for row in rows:
        cost = count_tokens(row) + 1
        if group and size + cost > budget:
            parts.append("\n".join([header] + group))
            group, size = [], 0
        group.append(row)
        size += cost
    if group:
        parts.append("\n".join([header] + group))
    return parts


def _paragraph_chunks(units: list[Unit], target: int, limit: int) -> list[tuple[str, str]]:
    """Group consecutive paragraphs; returns (text, locator) pairs."""
    chunks: list[tuple[str, str]] = []
    group: list[Unit] = []
    size = 0

    def flush() -> None:
        if not group:
            return
        first, last = group[0].paragraph, group[-1].paragraph
        locator = f"paragraph {first}" if first == last else f"paragraphs {first}-{last}"
        chunks.append(("\n\n".join(u.text for u in group), locator))

    for unit in units:
        cost = count_tokens(unit.text) + 1
        if group and (size + cost > limit or size >= target):
            flush()
            group, size = [], 0
        group.append(unit)
        size += cost
    flush()
    return chunks


def table_rows(text: str, header: str) -> list[str]:
    """Data rows of a serialised table: everything after the header except
    section labels, which are written in square brackets."""
    body = text[len(header) :] if header and text.startswith(header) else text
    return [line for line in body.strip("\n").splitlines() if line and not line.startswith("[")]


def chunk_document(
    info: DocumentInfo,
    units: list[Unit],
    target: int = TARGET_TOKENS,
    limit: int = MAX_TOKENS,
    overlap: int = OVERLAP_LINES,
) -> list[Chunk]:
    chunks: list[Chunk] = []

    def add(text: str, locator: str, chunk_type: str, page: int | None, parent: Chunk | None = None, prefix: str = "") -> Chunk:
        chunk = Chunk(
            chunk_id=f"{info.name}#{len(chunks) + 1}",
            text=text,
            embed_text=f"{_embed_header(info, locator)}\n{prefix}{text}",
            company=info.company,
            year=info.year,
            entity=info.entity,
            currency=info.currency,
            source_file=info.name,
            page=page,
            locator=locator,
            chunk_type=chunk_type,
            parent_id=parent.chunk_id if parent else None,
        )
        chunks.append(chunk)
        return chunk

    paragraph_units = [u for u in units if u.paragraph is not None]
    if paragraph_units:
        for text, locator in _paragraph_chunks(paragraph_units, target, limit):
            add(text, locator, "text", None)

    for unit in units:
        if unit.paragraph is not None:
            continue
        if unit.kind == "table":
            for text in _table_chunks(unit, limit):
                table = add(text, unit.locator, "table", unit.page)
                for row in table_rows(text, unit.table_header):
                    add(row, unit.locator, "row", unit.page, parent=table, prefix=f"{unit.table_header}\n")
        else:
            for window in split_lines(unit.text.splitlines(), target, limit, overlap):
                add("\n".join(window), unit.locator, "text", unit.page)
    return chunks
