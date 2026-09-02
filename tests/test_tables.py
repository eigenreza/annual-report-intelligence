"""Table detection and serialisation on the text layer."""

from src.ingest.parse import clean_lines, find_table_blocks, serialize_table

FIVE_YEAR_LINES = """BMW Group in Figures
FURTHER FINANCIAL PERFORMANCE FIGURES
in € million 2017 2018 2019 2020 2021 Change in %
Total capital expenditure 1 7,112 8,013 7,784 6,222 7,518 20.8
Group revenues 2 98,282 96,855 104,210 98,990 111,239 12.4
Automotive 85,742 85,846 91,682 80,853 95,476 18.1
Eliminations 2 – 17,306 – 18,875 – 19,443 – 14,194 – 19,857 39.9
Group net profit / loss 2 8,675 7,064 5,022 3,857 12,463 –""".splitlines()

KEY_METRICS_LINES = """COMPANY KEY METRICS
The table below shows our full year 2021 key metrics for the Company compared to a year ago.
2020 2021 H / (L)
GAAP Financial Measures
Cash Flows from Operating Activities ($B) $ 24.3 $ 15.8 $ (8.5)
Revenue ($M) 127,144 136,341 7 %
Net Income/(Loss) ($M) (1,279) 17,937 $ 19,216
Net Income/(Loss) Margin (%) (1.0) % 13.2 % 14.2 ppts""".splitlines()


def test_multi_year_overview_rows_are_labelled_by_year():
    blocks = find_table_blocks(FIVE_YEAR_LINES)
    assert len(blocks) == 1
    block = blocks[0]
    assert block.unit == "in € million"
    assert block.columns == ["2017", "2018", "2019", "2020", "2021", "Change in %"]
    head, body = serialize_table(block, 10)
    assert head.startswith("Table (page 10): BMW Group in Figures / FURTHER FINANCIAL PERFORMANCE FIGURES")
    assert "Units: in € million" in head
    assert (
        "Group revenues 2: 2017 = 98,282; 2018 = 96,855; 2019 = 104,210; 2020 = 98,990; "
        "2021 = 111,239; Change in % = 12.4"
    ) in body
    assert "Eliminations 2: 2017 = -17,306; 2018 = -18,875;" in body
    assert "Group net profit / loss 2: 2017 = 8,675; 2018 = 7,064; 2019 = 5,022; 2020 = 3,857; 2021 = 12,463; Change in % = –" in body


def test_change_column_currency_fragments_and_section_labels():
    blocks = find_table_blocks(KEY_METRICS_LINES)
    assert len(blocks) == 1
    head, body = serialize_table(blocks[0], 39)
    assert "COMPANY KEY METRICS" in head
    assert "Columns: 2020 | 2021 | H / (L)" in head
    assert "[GAAP Financial Measures]" in body
    assert "Cash Flows from Operating Activities ($B): 2020 = $24.3; 2021 = $15.8; H / (L) = $(8.5)" in body
    assert "Revenue ($M): 2020 = 127,144; 2021 = 136,341; H / (L) = 7%" in body
    assert "Net Income/(Loss) ($M): 2020 = (1,279); 2021 = 17,937; H / (L) = $19,216" in body
    assert "Net Income/(Loss) Margin (%): 2020 = (1.0)%; 2021 = 13.2%; H / (L) = 14.2ppts" in body


def test_untitled_tables_inherit_the_most_recent_heading():
    from src.ingest.parse import closing_labels

    previous_page = ["Some closing sentence of the section.", "Automotive Segment"]
    table = ["2019 2020 H / (L)", "Revenue ($M) $ 143,604 $ 115,894 $ (27,710)", "EBIT ($M) 4,888 1,706 (3,182)"]
    carried = closing_labels(previous_page)
    assert carried == ["Automotive Segment"]
    assert find_table_blocks(table, carried)[0].title == ["Automotive Segment"]
    assert find_table_blocks(table, None)[0].title == []
    own_heading = ["An introductory sentence.", "Regional heading"] + table
    assert find_table_blocks(own_heading, carried)[0].title == ["Regional heading"]
    two_tables = ["Ford Credit Segment"] + table + ["The next table shows the same metrics for the following year."] + table
    titles = [block.title for block in find_table_blocks(two_tables)]
    assert titles == [["Ford Credit Segment"], ["Ford Credit Segment"]]


def test_rows_without_a_fitting_header_keep_their_values_in_order():
    lines = ["Segment results", "Automotive 7,888 6,182", "Motorcycles 207 175"]
    blocks = find_table_blocks(lines)
    assert len(blocks) == 1
    head, body = serialize_table(blocks[0], 12)
    assert head == "Table (page 12): Segment results"
    assert body == "Automotive: 7,888 | 6,182\nMotorcycles: 207 | 175"


def test_prose_with_numbers_is_not_a_table():
    lines = [
        "Revenue increased by 7 % in 2021 compared with 2020.",
        "We sold 4 million vehicles and employed 180,000 people in 2021.",
        "Net income was 17,937 million dollars.",
    ]
    assert find_table_blocks(lines) == []


def test_doubled_character_footers_and_stray_whitespace_are_removed():
    text = "Revenue 100\n118811000099__FFoorrdd__22002211__AARR..iinndddd 4433 22//44//2222 11::5500 PPMM\n  spaced   words "
    assert clean_lines(text) == ["Revenue 100", "spaced words"]
