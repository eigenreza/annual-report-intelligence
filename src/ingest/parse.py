"""Per-file extraction.

Each PDF page yields its running text plus one unit per detected table. Two
corrections happen on the way:

* Duplicate text layer removal. Some PDFs render a page's text twice, and the
  second rendering is not always a rigid copy: it can be shifted, or re-typeset at
  a slightly different size so that lines reflow. Geometry is therefore not a
  reliable signature, but the character stream is. When the opening characters of a
  page reappear later in the stream and nearly everything after that point repeats
  text before it, the second run is a duplicate layer and is dropped before any
  text is read.
* Table detection on the text layer. The reports use borderless tables that the
  geometric table finder either misses or fragments, so tables are recognised from
  lines that end in numeric columns, ideally under a header of years or period
  labels, and each row is re-serialised with its column label attached to every value.

DOCX files have no pages, so each non-empty paragraph becomes a unit located by
its paragraph number.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path

import docx
import pdfplumber

YEAR_RE = re.compile(r"(19|20)\d{2}")
NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
# Hyphen, en dash, em dash and minus sign: all used as negative signs or empty-cell placeholders in the reports.
DASHES = {"-", "\u2013", "\u2014", "\u2212"}
PLACEHOLDERS = DASHES | {"n/a", "N/A", "n.m.", "n.m", "NM", "*"}
VALUE_SUFFIXES = ("ppts", "pts", "pp", "%")

DUPLICATE_PROBE = 60
DUPLICATE_MIN_COVERAGE = 0.8


@dataclass
class Unit:
    """One extracted piece of a document together with its location."""

    text: str
    kind: str
    locator: str
    page: int | None = None
    paragraph: int | None = None
    table_header: str = ""
    dedup_applied: bool = False
    dedup_coverage: float = 0.0


@dataclass
class TableBlock:
    title: list[str]
    unit: str
    columns: list[str]
    rows: list[tuple[str, list[str] | str]] = field(default_factory=list)


# --- duplicate text layer removal --------------------------------------------------


def find_duplicate_layer(
    chars: list[dict], probe: int = DUPLICATE_PROBE, min_coverage: float = DUPLICATE_MIN_COVERAGE
) -> tuple[int, float] | None:
    """Locate a second rendering of the page text inside the character stream.

    Returns (index of the first repeated character, coverage) or None. The text is
    read in stream order with whitespace dropped. Every later occurrence of the
    opening characters is a candidate split; the split wins when the share of the
    remaining stream that matches text before it reaches min_coverage. Judging by
    coverage rather than by similarity of the two halves keeps a page that repeats
    one section but renders another only once.
    """
    visible = [i for i, char in enumerate(chars) if not char["text"].isspace()]
    text = "".join(chars[i]["text"] for i in visible)
    if len(text) < 2 * probe:
        return None
    head = text[:probe]
    best: tuple[int, float] | None = None
    start = probe
    while (k := text.find(head, start)) >= 0:
        first, second = text[:k], text[k:]
        if len(second) >= probe:
            matcher = difflib.SequenceMatcher(None, first, second, autojunk=False)
            matched = sum(block.size for block in matcher.get_matching_blocks())
            coverage = matched / len(second)
            if best is None or coverage > best[1]:
                best = (k, coverage)
        start = k + 1
    if best is None or best[1] < min_coverage:
        return None
    return visible[best[0]], best[1]


def dedup_page(page):
    """Return (page, coverage, applied). When a duplicate layer is found, the
    returned page is a filtered view without the repeated characters."""
    chars = page.chars
    found = find_duplicate_layer(chars)
    if found is None:
        return page, 0.0, False
    split, coverage = found
    copy_ids = {id(char) for char in chars[split:]}
    filtered = page.filter(lambda obj: obj["object_type"] != "char" or id(obj) not in copy_ids)
    return filtered, coverage, True


# --- line cleaning ---------------------------------------------------------------


def _is_doubled_artifact(line: str) -> bool:
    """Print-shop footers where every character is repeated ("FFoorrdd 22002211")."""
    tokens = [t for t in line.split() if len(t) >= 2]
    return len(tokens) >= 3 and all(t[0::2] == t[1::2] for t in tokens)


def clean_lines(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if line and not _is_doubled_artifact(line):
            lines.append(line)
    return lines


# --- table detection on the text layer ---------------------------------------------


def _is_value(token: str) -> bool:
    if token in PLACEHOLDERS:
        return True
    core = token.replace("$", "").replace("(", "").replace(")", "").replace("€", "")
    for suffix in VALUE_SUFFIXES:
        if core.endswith(suffix):
            core = core[: -len(suffix)]
            break
    core = core.lstrip("-")
    return bool(core) and NUMBER_RE.fullmatch(core) is not None


def _merge_tokens(tokens: list[str]) -> list[str]:
    """Join sign, currency and unit fragments to their numbers so that
    "– 17,306", "$ 24.3" and "14.2 ppts" each become one token."""
    out: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if token in DASHES and nxt and _is_value(nxt) and nxt not in PLACEHOLDERS:
            out.append("-" + nxt)
            i += 2
            continue
        if token == "$" and nxt and _is_value(nxt):
            out.append(nxt if nxt in PLACEHOLDERS else "$" + nxt)
            i += 2
            continue
        if token in VALUE_SUFFIXES and out and _is_value(out[-1]) and out[-1] not in PLACEHOLDERS:
            out[-1] += token
            i += 1
            continue
        out.append(token)
        i += 1
    return out


def _split_row(tokens: list[str]) -> tuple[list[str], list[str]]:
    """Split a line into label tokens and the run of values at its end."""
    end = len(tokens)
    while end > 0 and _is_value(tokens[end - 1]):
        end -= 1
    return tokens[:end], tokens[end:]


def _is_data_row(tokens: list[str]) -> bool:
    label, values = _split_row(tokens)
    return len(values) >= 2 and any(ch.isalpha() for ch in " ".join(label))


def _is_year(token: str) -> bool:
    return YEAR_RE.fullmatch(token.strip(",.")) is not None


def _header_columns(tokens: list[str]) -> tuple[str, list[str]] | None:
    """Recognise a column header: two or more consecutive year tokens, with an
    optional unit label before them and an optional extra column after them."""
    year_positions = [i for i, t in enumerate(tokens) if _is_year(t)]
    if len(year_positions) < 2 or len(tokens) > 14:
        return None
    first, last = year_positions[0], year_positions[-1]
    span = tokens[first : last + 1]
    if not all(_is_year(t) for t in span):
        return None
    unit = " ".join(tokens[:first]).strip(" ,:")
    columns = [t.strip(",.") for t in span]
    extra = " ".join(tokens[last + 1 :]).strip()
    if extra:
        columns.append(extra)
    return unit, columns


def _header_fits(columns: list[str], rows: list[tuple[str, list[str] | str]]) -> bool:
    """A header applies when most data rows carry as many values as it has columns,
    allowing one extra leading token for footnote markers."""
    counts = [len(_split_row(payload)[1]) for kind, payload in rows if kind == "row"]
    fitting = sum(1 for c in counts if c in (len(columns), len(columns) + 1))
    return fitting * 2 >= len(counts)


def _is_label_line(line: str) -> bool:
    """A short line of words without sentence punctuation: a heading or a section
    label rather than prose or data."""
    tokens = _merge_tokens(line.split())
    return (
        len(line) <= 80
        and not line.endswith(".")
        and any(ch.isalpha() for ch in line)
        and not _is_data_row(tokens)
        and _header_columns(tokens) is None
    )


TITLE_LOOKBACK = 12


def _title_lines(lines: list[str], before: int) -> list[str]:
    """Up to two label lines above a table.

    Introductory sentences and the rows of a preceding table are skipped, because
    the heading that names a table (such as a segment name) often sits several
    lines above its header. A segment table serialised without its heading reads
    like a company total, which is worse than a slightly distant title.
    """
    title: list[str] = []
    for j in range(before - 1, max(-1, before - TITLE_LOOKBACK - 1), -1):
        line = lines[j]
        if _is_label_line(line):
            title.insert(0, line)
            if len(title) == 2:
                break
        elif title:
            break
    return title


def find_table_blocks(lines: list[str], carried_title: list[str] | None = None) -> list[TableBlock]:
    """Detect tables in a page's lines.

    A table without a heading of its own inherits the most recent one: the heading
    of the previous table on the page, or `carried_title`, the heading lines that
    closed the previous page. Reports put several tables under one section
    heading and page breaks separate tables from their headings, and a segment
    table without its heading reads like a company total.
    """
    blocks: list[TableBlock] = []
    header: tuple[str, list[str]] | None = None
    header_at: int | None = None
    last_title: list[str] = list(carried_title or [])
    i = 0
    while i < len(lines):
        tokens = _merge_tokens(lines[i].split())
        found = _header_columns(tokens)
        if found:
            header, header_at = found, i
            i += 1
            continue
        if not _is_data_row(tokens):
            i += 1
            continue
        start = i
        rows: list[tuple[str, list[str] | str]] = []
        gap = 0
        while i < len(lines):
            line = lines[i]
            tokens = _merge_tokens(line.split())
            if _header_columns(tokens):
                break
            if _is_data_row(tokens):
                rows.append(("row", tokens))
                gap = 0
            elif gap == 0 and _is_label_line(line):
                # A short label between rows is a section heading inside the table.
                rows.append(("subheading", line))
                gap = 1
            else:
                break
            i += 1
        while rows and rows[-1][0] == "subheading":
            rows.pop()
        if sum(1 for kind, _ in rows if kind == "row") < 2:
            continue
        if header_at is not None and start - header_at == 2 and _is_label_line(lines[start - 1]):
            # A section label directly under the header belongs to the first rows.
            rows.insert(0, ("subheading", lines[start - 1]))
        use_header = header is not None and _header_fits(header[1], rows)
        if use_header:
            unit, columns = header
            title = _title_lines(lines, header_at)
        else:
            unit, columns = "", []
            title = _title_lines(lines, start)
        if not title:
            title = list(last_title)
        last_title = title
        blocks.append(TableBlock(title=title, unit=unit, columns=columns, rows=rows))
    return blocks


def closing_labels(lines: list[str]) -> list[str]:
    """Heading lines at the very end of a page, to carry over to the next page."""
    return _title_lines(lines, len(lines))


def serialize_table(block: TableBlock, page: int | None) -> tuple[str, str]:
    """Render a block as (header text, body text). The header names the table and
    its columns and is repeated when a long table is split into several chunks."""
    where = f"page {page}" if page is not None else "document"
    head = [f"Table ({where})" + (f": {' / '.join(block.title)}" if block.title else "")]
    if block.unit:
        head.append(f"Units: {block.unit}")
    if block.columns:
        head.append("Columns: " + " | ".join(block.columns))
    body: list[str] = []
    for kind, payload in block.rows:
        if kind == "subheading":
            body.append(f"[{payload}]")
            continue
        label, values = _split_row(payload)
        n = len(block.columns)
        if n and len(values) == n + 1:
            label, values = label + values[:1], values[1:]
        if n and len(values) == n:
            pairs = "; ".join(f"{c} = {v}" for c, v in zip(block.columns, values))
            body.append(f"{' '.join(label)}: {pairs}")
        else:
            body.append(f"{' '.join(label)}: {' | '.join(values)}")
    return "\n".join(head), "\n".join(body)


# --- document parsers ------------------------------------------------------------


def parse_pdf(path: Path) -> list[Unit]:
    units: list[Unit] = []
    carried: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            source, coverage, applied = dedup_page(page)
            lines = clean_lines(source.extract_text() or "")
            if not lines:
                continue
            locator = f"page {number}"
            units.append(
                Unit("\n".join(lines), "text", locator, page=number, dedup_applied=applied, dedup_coverage=coverage)
            )
            blocks = find_table_blocks(lines, carried)
            carried = closing_labels(lines)
            for block in blocks:
                head, body = serialize_table(block, number)
                units.append(
                    Unit(
                        f"{head}\n{body}",
                        "table",
                        locator,
                        page=number,
                        table_header=head,
                        dedup_applied=applied,
                        dedup_coverage=coverage,
                    )
                )
    return units


def parse_docx(path: Path) -> list[Unit]:
    document = docx.Document(str(path))
    units: list[Unit] = []
    for number, paragraph in enumerate(document.paragraphs, start=1):
        text = " ".join(paragraph.text.split())
        if text:
            units.append(Unit(text, "text", f"paragraph {number}", paragraph=number))
    return units


def parse_document(path: Path) -> list[Unit]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return parse_pdf(path)
    if suffix == ".docx":
        return parse_docx(path)
    raise ValueError(f"Unsupported file type: {path.name}")
