"""The duplicate text layer detector on synthetic character streams."""

from src.ingest.parse import find_duplicate_layer

PROSE = " ".join(f"Sentence {i} discusses item {i * 7} of the report in some detail." for i in range(40))
SECTION = " ".join(f"Risk {i} concerns supplier {i * 3} and its delivery schedule." for i in range(30))
FOOTER = "199174_Report_2023_AR.indd 48 2/9/24 2:55 PM"


def stream(text: str) -> list[dict]:
    return [{"text": ch} for ch in text]


def kept_text(chars: list[dict]) -> str:
    found = find_duplicate_layer(chars)
    assert found is not None
    split, _coverage = found
    return "".join(c["text"] for c in chars[:split])


def test_clean_page_is_not_flagged():
    assert find_duplicate_layer(stream(PROSE)) is None


def test_short_page_is_not_flagged():
    assert find_duplicate_layer(stream("Total revenues 96,773 81,462")) is None


def test_repeated_heading_without_repeated_body_is_not_flagged():
    heading = "Item 1A. Risk Factors (Continued) " * 3
    assert find_duplicate_layer(stream(heading + PROSE + heading + SECTION)) is None


def test_doubled_layer_is_split_at_the_second_rendering():
    chars = stream(PROSE + " " + PROSE)
    found = find_duplicate_layer(chars)
    assert found is not None
    split, coverage = found
    assert split == len(PROSE) + 1
    assert coverage == 1.0
    assert kept_text(chars).strip() == PROSE


def test_footer_rendered_once_does_not_prevent_detection():
    chars = stream(PROSE + " " + PROSE + " " + FOOTER)
    assert kept_text(chars).strip() == PROSE


def test_partially_doubled_page_keeps_the_section_rendered_once():
    chars = stream(PROSE + " " + SECTION + " " + PROSE)
    assert kept_text(chars).strip() == PROSE + " " + SECTION
