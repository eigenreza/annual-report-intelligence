from src.answer.answer import Answer, cited_hits, extract_citations
from src.index.retrieve import Hit
from src.ingest.chunk import Chunk


def chunk(source_file: str, locator: str, page: int | None = None) -> Chunk:
    return Chunk(
        chunk_id=f"{source_file}#{locator}",
        text="",
        embed_text="",
        company=None,
        year=None,
        entity="x",
        currency=None,
        source_file=source_file,
        page=page,
        locator=locator,
        chunk_type="text",
    )


def test_citation_forms_are_recognised():
    text = (
        "Revenue was 96,773 million USD (Tesla_Annual_Report_2023.pdf, page 20). "
        "Ford reported 127,144 (Ford_Annual_Report_2021.pdf, p. 39) and the overview "
        "(BMW_Annual_Report_2021.pdf, pages 10 and 11) confirms it (news.docx, paragraphs 1-5). "
        "Both agree (Tesla_Annual_Report_2023.pdf, page 20; Ford_Annual_Report_2023.pdf, page 44)."
    )
    assert extract_citations(text) == [
        ("Tesla_Annual_Report_2023.pdf", {20}),
        ("Ford_Annual_Report_2021.pdf", {39}),
        ("BMW_Annual_Report_2021.pdf", {10, 11}),
        ("news.docx", {1, 2, 3, 4, 5}),
        ("Tesla_Annual_Report_2023.pdf", {20}),
        ("Ford_Annual_Report_2023.pdf", {44}),
    ]


def test_citations_joined_by_and_and_sources_lines_are_recognised():
    text = (
        "Ford had a net loss (Ford_Annual_Report_2023.pdf, page 54 and Ford_Annual_Report_2022.pdf, page 39).\n"
        "Sources: BMW_Annual_Report_2021.pdf, pages 9-11; BMW_Annual_Report_2023.pdf, page 10."
    )
    assert extract_citations(text) == [
        ("Ford_Annual_Report_2023.pdf", {54}),
        ("Ford_Annual_Report_2022.pdf", {39}),
        ("BMW_Annual_Report_2021.pdf", {9, 10, 11}),
        ("BMW_Annual_Report_2023.pdf", {10}),
    ]


def test_page_word_repeated_inside_one_citation():
    text = "From the income statement (Tesla_Annual_Report_2023.pdf, page 20 and page 21)."
    assert extract_citations(text) == [("Tesla_Annual_Report_2023.pdf", {20, 21})]


def test_prose_citation_form_is_recognised():
    text = "These figures come from the table on page 10 of BMW_Annual_Report_2021.pdf and page 4 in Tesla_Annual_Report_2023.pdf."
    assert extract_citations(text) == [("BMW_Annual_Report_2021.pdf", {10}), ("Tesla_Annual_Report_2023.pdf", {4})]


def test_cited_hits_match_file_and_location():
    hits = [
        Hit(chunk("Tesla_Annual_Report_2023.pdf", "page 20", 20), 0.9),
        Hit(chunk("Tesla_Annual_Report_2023.pdf", "page 4", 4), 0.8),
        Hit(chunk("news.docx", "paragraphs 3-7"), 0.7),
        Hit(chunk("news.docx", "paragraphs 9-12"), 0.6),
    ]
    text = "Figures (Tesla_Annual_Report_2023.pdf, page 20) and context (news.docx, paragraphs 1-5)."
    cited = cited_hits(text, hits)
    assert [h.chunk.locator for h in cited] == ["page 20", "paragraphs 3-7"]


def test_sources_fall_back_to_all_retrieved_chunks_without_citations():
    hits = [Hit(chunk("a.pdf", "page 1", 1), 0.9), Hit(chunk("a.pdf", "page 1", 1), 0.8), Hit(chunk("b.pdf", "page 2", 2), 0.7)]
    answer = Answer(text="No figures here.", hits=hits, cited=[])
    assert not answer.has_citations
    assert answer.sources() == ["a.pdf, page 1 (x, text)", "b.pdf, page 2 (x, text)"]
    answer = Answer(text="Figure (b.pdf, page 2).", hits=hits, cited=cited_hits("Figure (b.pdf, page 2).", hits))
    assert answer.sources() == ["b.pdf, page 2 (x, text)"]
