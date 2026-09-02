from src.index.retrieve import Hit, collapse_to_parents, keyword_overlap, keywords, parse_query, restrict_to_latest
from src.ingest.chunk import Chunk


def test_current_state_questions_use_only_the_latest_report():
    assert parse_query("Which Tesla product is currently in the development stage?").latest_only
    assert not parse_query("Which Tesla product was in development in 2022?").latest_only
    assert not parse_query("How much revenue did Tesla generate in 2023?").latest_only
    old = Chunk("a", "", "", "Tesla", 2022, "Tesla, Inc.", "USD", "t22.pdf", 3, "page 3", "text")
    new = Chunk("b", "", "", "Tesla", 2023, "Tesla, Inc.", "USD", "t23.pdf", 4, "page 4", "text")
    news = Chunk("c", "", "", None, None, "news", None, "news.pdf", 1, "page 1", "text")
    kept = restrict_to_latest([Hit(old, 0.9), Hit(new, 0.8), Hit(news, 0.7)], {"Tesla": 2023})
    assert [h.chunk.chunk_id for h in kept] == ["b", "c"]


def make_chunk(chunk_id: str, chunk_type: str, parent_id: str | None = None) -> Chunk:
    return Chunk(chunk_id, "", "", "Ford", 2021, "Ford Motor Company", "USD", "f.pdf", 1, "page 1", chunk_type, parent_id)


def test_row_hits_resolve_to_their_table_with_the_best_score():
    table = make_chunk("f.pdf#1", "table")
    row_a = make_chunk("f.pdf#2", "row", parent_id="f.pdf#1")
    row_b = make_chunk("f.pdf#3", "row", parent_id="f.pdf#1")
    text = make_chunk("f.pdf#4", "text")
    by_id = {c.chunk_id: c for c in (table, row_a, row_b, text)}
    hits = collapse_to_parents([(text, 0.70), (row_a, 0.75), (table, 0.60), (row_b, 0.80)], by_id)
    assert [(h.chunk.chunk_id, h.score) for h in hits] == [("f.pdf#1", 0.80), ("f.pdf#4", 0.70)]


def test_keywords_drop_function_words_and_match_by_prefix():
    keys = keywords("What was Ford's revenue for the year 2020?")
    assert keys == {"ford", "revenu", "2020"}
    assert keyword_overlap(keys, "Ford Motor Company, page 39\nRevenue ($M): 2020 = 127,144") == 1.0
    assert keyword_overlap(keys, "Ford Motor Company, page 66\nTotal assets December 31, 2020") == 2 / 3
    assert keywords("Which company recorded better profitability in 2022 overall?") == {"better", "profit", "2022"}
    assert keyword_overlap(set(), "anything") == 0.0


def test_companies_and_years_are_extracted():
    info = parse_query("Between Tesla and Ford, which company achieved higher profits in 2022?")
    assert info.companies == ["Ford", "Tesla"]
    assert info.years == [2022]


def test_possessives_and_case_do_not_hide_a_company():
    assert parse_query("What was bmw's revenue for 2017 and 2020?").companies == ["BMW"]
    assert parse_query("What was bmw's revenue for 2017 and 2020?").years == [2017, 2020]


def test_metric_words_are_expanded_for_retrieval_only():
    info = parse_query("What were Tesla's profit numbers for 2022?")
    assert info.text == "What were Tesla's profit numbers for 2022?"
    assert "net income" in info.expanded
    plain = parse_query("Which Tesla product is currently in the development stage?")
    assert plain.expanded == plain.text


def test_unnamed_company_question_routes_to_every_company():
    info = parse_query("Which company recorded better profitability in 2022 overall?")
    assert info.companies == ["BMW", "Ford", "Tesla"]


def test_question_without_any_company_reference_is_not_routed():
    info = parse_query("By how much did the central bank raise interest rates?")
    assert info.companies == []
