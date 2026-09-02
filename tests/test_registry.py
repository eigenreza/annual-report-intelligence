from pathlib import Path

import pytest

from src.ingest.registry import COMPANIES, REGISTRY, lookup, missing_documents, registered_documents


def test_entries_are_complete_and_consistent():
    names = [info.name for info in REGISTRY]
    assert len(names) == len(set(names))
    for info in REGISTRY:
        assert info.entity
        if info.company is None:
            assert info.year is None and info.entity == "news" and info.note
        else:
            assert info.year is not None and info.currency in ("EUR", "USD")


def test_reporting_entity_is_independent_of_file_name():
    assert lookup("BMW_Annual_Report_2021.pdf").entity == "BMW Group"
    assert lookup("BMW_Annual_Report_2022.pdf").entity == "BMW Finance N.V."
    assert lookup("BMW_Annual_Report_2023.pdf").entity == "BMW Finance N.V."
    assert "subsidiary" in lookup("BMW_Annual_Report_2023.pdf").note


def test_lookup_accepts_full_paths():
    info = lookup(Path("data") / "Tesla" / "Tesla_Annual_Report_2023.pdf")
    assert (info.company, info.year, info.currency) == ("Tesla", 2023, "USD")


def test_unregistered_file_is_rejected():
    with pytest.raises(KeyError):
        lookup("Unknown_Report_2024.pdf")


def test_registered_documents_returns_only_existing_files(tmp_path):
    (tmp_path / "news.pdf").write_bytes(b"")
    found = registered_documents(tmp_path)
    assert [info.name for info, _ in found] == ["news.pdf"]
    assert len(missing_documents(tmp_path)) == len(REGISTRY) - 1


def test_company_list():
    assert COMPANIES == ("BMW", "Ford", "Tesla")
