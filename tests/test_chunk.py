from src.ingest.chunk import MAX_TOKENS, chunk_document, count_tokens, split_lines
from src.ingest.parse import Unit
from src.ingest.registry import lookup


def test_text_and_table_chunks_carry_document_metadata():
    info = lookup("Ford_Annual_Report_2021.pdf")
    header = "Table (page 3)\nColumns: 2020 | 2021"
    units = [
        Unit("Revenue grew.\nCosts fell.", "text", "page 3", page=3),
        Unit(f"{header}\nRevenue: 2020 = 1; 2021 = 2", "table", "page 3", page=3, table_header=header),
    ]
    chunks = chunk_document(info, units)
    assert {c.chunk_type for c in chunks} == {"text", "table", "row"}
    for c in chunks:
        assert (c.company, c.year, c.entity, c.currency) == ("Ford", 2021, "Ford Motor Company", "USD")
        assert (c.source_file, c.page, c.locator) == ("Ford_Annual_Report_2021.pdf", 3, "page 3")
        assert c.header == "[Ford_Annual_Report_2021.pdf | Ford Motor Company | page 3]"
        assert c.embed_text.startswith("Ford Motor Company annual report 2021 (Ford, figures in USD), page 3")
        assert c.embed_text.endswith(c.text)
    ids = [c.chunk_id for c in chunks]
    assert len(ids) == len(set(ids))


def test_table_rows_become_children_that_point_at_their_table():
    info = lookup("Ford_Annual_Report_2021.pdf")
    header = "Table (page 39): COMPANY KEY METRICS\nColumns: 2020 | 2021"
    text = f"{header}\n[GAAP Financial Measures]\nRevenue ($M): 2020 = 127,144; 2021 = 136,341\nNet Income ($M): 2020 = (1,279); 2021 = 17,937"
    chunks = chunk_document(info, [Unit(text, "table", "page 39", page=39, table_header=header)])
    tables = [c for c in chunks if c.chunk_type == "table"]
    rows = [c for c in chunks if c.chunk_type == "row"]
    assert len(tables) == 1 and tables[0].parent_id is None
    assert [r.text for r in rows] == [
        "Revenue ($M): 2020 = 127,144; 2021 = 136,341",
        "Net Income ($M): 2020 = (1,279); 2021 = 17,937",
    ]
    for row in rows:
        assert row.parent_id == tables[0].chunk_id
        assert (row.page, row.locator, row.company) == (39, "page 39", "Ford")
        assert row.embed_text == f"Ford Motor Company annual report 2021 (Ford, figures in USD), page 39\n{header}\n{row.text}"


def test_long_table_is_split_by_rows_with_header_repeated():
    header = "Table (page 9): Long\nColumns: 2022 | 2023"
    rows = [f"Line item number {i}: 2022 = {i}; 2023 = {i + 1}" for i in range(400)]
    unit = Unit(header + "\n" + "\n".join(rows), "table", "page 9", page=9, table_header=header)
    chunks = [c for c in chunk_document(lookup("Tesla_Annual_Report_2023.pdf"), [unit]) if c.chunk_type == "table"]
    assert len(chunks) > 1
    for c in chunks:
        assert c.text.startswith(header)
        assert count_tokens(c.text) <= MAX_TOKENS
    joined = "\n".join(c.text for c in chunks)
    assert all(joined.count(row) == 1 for row in rows)


def test_text_windows_respect_limit_and_overlap():
    lines = [f"Sentence number {i} ends here." for i in range(200)]
    windows = split_lines(lines, target=100, limit=130, overlap=2)
    assert len(windows) > 1
    assert all(count_tokens("\n".join(w)) <= 130 for w in windows)
    assert all(a[-2:] == b[:2] for a, b in zip(windows, windows[1:]))
    assert {line for w in windows for line in w} == set(lines)


def test_docx_paragraphs_get_paragraph_range_locators():
    info = lookup("news.docx")
    units = [Unit(f"Paragraph {i} text.", "text", f"paragraph {i}", paragraph=i) for i in (1, 3, 5)]
    chunks = chunk_document(info, units)
    assert len(chunks) == 1
    chunk = chunks[0]
    assert (chunk.locator, chunk.page, chunk.entity, chunk.company) == ("paragraphs 1-5", None, "news", None)
    assert chunk.header == "[news.docx | news | paragraphs 1-5]"
