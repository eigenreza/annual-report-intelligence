import numpy as np

from src.index.retrieve import (
    BM25,
    Hit,
    alias_phrases,
    collapse_to_parents,
    demote_segment_tables,
    diversify,
    metric_terms,
    parse_query,
    row_label_tokens,
    query_tokens,
    reciprocal_rank_fusion,
    restrict_to_latest,
    tokenize,
)
from src.ingest.chunk import Chunk


def make_chunk(chunk_id: str, chunk_type: str, parent_id: str | None = None) -> Chunk:
    return Chunk(chunk_id, "", "", "Ford", 2021, "Ford Motor Company", "USD", "f.pdf", 1, "page 1", chunk_type, parent_id)


def test_companies_and_years_are_extracted():
    info = parse_query("Between Tesla and Ford, which company achieved higher profits in 2022?")
    assert info.companies == ["Ford", "Tesla"]
    assert info.years == [2022]


def test_possessives_and_case_do_not_hide_a_company():
    info = parse_query("What was bmw's revenue for 2017 and 2020?")
    assert info.companies == ["BMW"]
    assert info.years == [2017, 2020]


def test_metric_words_are_expanded_for_retrieval_only():
    info = parse_query("What were Tesla's profit numbers for 2022?")
    assert info.text == "What were Tesla's profit numbers for 2022?"
    assert info.metrics == ["profit"]
    assert "net income" in info.expanded
    assert "income" in info.lexical and "total" not in info.lexical and "before" not in info.lexical
    plain = parse_query("Which Tesla product is currently in the development stage?")
    assert plain.expanded == plain.text and plain.metrics == []


def test_metric_words_match_by_prefix_and_segment_questions_are_not_company_level():
    assert parse_query("Which company recorded better profitability in 2022 overall?").metrics == ["profit"]
    assert parse_query("What were Ford's revenues in 2021?").company_level
    assert not parse_query("What was the revenue of Ford's Automotive segment in 2020?").company_level
    assert not parse_query("Which Tesla product is currently in the development stage?").company_level


def test_entity_aliases_are_whole_phrases_keyed_by_the_declaring_entity():
    aliases = alias_phrases(["revenue"])
    assert aliases == {"BMW Finance N.V.": [{"intere", "income"}]}
    assert alias_phrases(["margin"]) == {}
    label = row_label_tokens(Chunk("r", "Interest income third parties: 2023 = 427,471", "", "BMW", 2023, "BMW Finance N.V.", "EUR", "b.pdf", 10, "page 10", "row", "t"))
    assert any(phrase <= label for phrase in aliases["BMW Finance N.V."])
    loans = row_label_tokens(Chunk("r", "Loans due to banks interest rate: 2023 = 4", "", "BMW", 2023, "BMW Finance N.V.", "EUR", "b.pdf", 26, "page 26", "row", "t"))
    assert not any(phrase <= loans for phrase in aliases["BMW Finance N.V."])


def test_segment_tables_are_demoted_and_repeated_titles_capped():
    def table(chunk_id: str, title: str) -> Chunk:
        return Chunk(chunk_id, f"Table (page 1): {title}\nRevenue: 1", "", "Ford", 2021, "Ford Motor Company", "USD", "f.pdf", 1, "page 1", "table")

    regional = Hit(table("r", "China (Including Taiwan)"), 0.9)
    company = Hit(table("c", "COMPANY KEY METRICS"), 0.6)
    assert [h.chunk.chunk_id for h in demote_segment_tables([regional, company])] == ["c", "r"]
    repeats = [Hit(table(f"n{i}", "North America"), 0.9 - i / 100) for i in range(4)] + [company]
    assert [h.chunk.chunk_id for h in diversify(repeats, 3)] == ["n0", "n1", "c"]


def test_unnamed_company_question_routes_to_every_company():
    info = parse_query("Which company recorded better profitability in 2022 overall?")
    assert info.companies == ["BMW", "Ford", "Tesla"]


def test_question_without_any_company_reference_is_not_routed():
    info = parse_query("By how much did the central bank raise interest rates?")
    assert info.companies == []


def test_current_state_questions_use_only_the_latest_report():
    assert parse_query("Which Tesla product is currently in the development stage?").latest_only
    assert not parse_query("Which Tesla product was in development in 2022?").latest_only
    assert not parse_query("How much revenue did Tesla generate in 2023?").latest_only
    old = Chunk("a", "", "", "Tesla", 2022, "Tesla, Inc.", "USD", "t22.pdf", 3, "page 3", "text")
    new = Chunk("b", "", "", "Tesla", 2023, "Tesla, Inc.", "USD", "t23.pdf", 4, "page 4", "text")
    news = Chunk("c", "", "", None, None, "news", None, "news.pdf", 1, "page 1", "text")
    kept = restrict_to_latest([Hit(old, 0.9), Hit(new, 0.8), Hit(news, 0.7)], {"Tesla": 2023})
    assert [h.chunk.chunk_id for h in kept] == ["b", "c"]


def test_tokens_are_prefix_stemmed_and_query_tokens_drop_function_words():
    assert tokenize("Revenues ($M): 2020 = 127,144") == ["revenu", "2020", "127", "144"]
    assert query_tokens("What was Ford's revenue for the year 2020?") == ["ford", "revenu", "2020"]
    assert query_tokens("Provide a summary of revenue figures for Tesla, BMW, and Ford") == ["revenu", "tesla", "bmw", "ford"]


def test_metric_terms_and_row_labels():
    info = parse_query("Which company recorded better profitability in 2022 overall?")
    assert {"profit", "income", "ebit"} <= metric_terms(info)
    row = Chunk("r", "Net Income/(Loss) ($M): 2022 = (1,981); 2023 = 4,347", "", "Ford", 2023, "Ford Motor Company", "USD", "f.pdf", 44, "page 44", "row", "t")
    assert row_label_tokens(row) == {"net", "income", "loss"}
    assert row_label_tokens(row) & metric_terms(info)
    table = Chunk("t", "Table (page 44)\nNet income: 1", "", "Ford", 2023, "Ford Motor Company", "USD", "f.pdf", 44, "page 44", "table")
    assert row_label_tokens(table) == set()


def test_bm25_prefers_the_row_that_contains_the_metric_word():
    docs = [
        tokenize("Ford page 39 Revenue ($M): 2020 = 127,144; 2021 = 136,341"),
        tokenize("Ford page 63 Receivables: 2019 = $(0.1); 2020 = $0.4; 2021 = $(0.2)"),
        tokenize("Ford page 5 wholesale unit volumes and market share in 2020 and 2021 across regions and segments"),
    ]
    scores = BM25(docs).scores(query_tokens("What was Ford's revenue for the year 2020?"))
    assert scores[0] > scores[1] > 0
    assert scores[0] > scores[2]


def test_reciprocal_rank_fusion_rewards_agreement_and_tolerates_absence():
    fused = reciprocal_rank_fusion([0, 1, 2], [1, 0], size=4, k=1)
    assert np.argmax(fused) in (0, 1)
    assert fused[1] > fused[2] > fused[3]
    assert fused[3] > 0


def test_row_hits_resolve_to_their_table_with_the_best_score():
    table = make_chunk("f.pdf#1", "table")
    row_a = make_chunk("f.pdf#2", "row", parent_id="f.pdf#1")
    row_b = make_chunk("f.pdf#3", "row", parent_id="f.pdf#1")
    text = make_chunk("f.pdf#4", "text")
    by_id = {c.chunk_id: c for c in (table, row_a, row_b, text)}
    hits = collapse_to_parents([(text, 0.70), (row_a, 0.75), (table, 0.60), (row_b, 0.80)], by_id)
    assert [(h.chunk.chunk_id, h.score) for h in hits] == [("f.pdf#1", 0.80), ("f.pdf#4", 0.70)]
