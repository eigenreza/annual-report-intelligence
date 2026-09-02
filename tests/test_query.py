from src.index.retrieve import parse_query


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


def test_no_company_means_no_routing():
    info = parse_query("Which company recorded better profitability in 2022 overall?")
    assert info.companies == []
