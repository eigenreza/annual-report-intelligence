"""The document registry.

Every source file is registered here with its company, reporting year, reporting
entity and currency. File names do not always describe the reporting entity: a file
named after a group can contain the accounts of a subsidiary, and those figures must
never be presented as group figures. Recording the entity once, explicitly, lets the
rest of the pipeline label every chunk and lets the answer layer explain what the
corpus covers.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DocumentInfo:
    source_file: str
    company: str | None
    year: int | None
    entity: str
    currency: str | None
    note: str = ""

    @property
    def name(self) -> str:
        return Path(self.source_file).name

    @property
    def label(self) -> str:
        """Short human-readable description used in prompts and terminal output."""
        if self.company is None:
            return f"{self.name}: {self.note}"
        return f"{self.name}: {self.entity}, reporting year {self.year}, figures in {self.currency}"


BMW_FINANCE_NOTE = (
    "Annual report of BMW Finance N.V., a Netherlands-based financing subsidiary of the "
    "BMW Group. It reports the subsidiary's own interest income and net result. "
    "BMW Group revenue and profit are not part of this document."
)

REGISTRY: tuple[DocumentInfo, ...] = (
    DocumentInfo("BMW/BMW_Annual_Report_2021.pdf", "BMW", 2021, "BMW Group", "EUR"),
    DocumentInfo("BMW/BMW_Annual_Report_2022.pdf", "BMW", 2022, "BMW Finance N.V.", "EUR", BMW_FINANCE_NOTE),
    DocumentInfo("BMW/BMW_Annual_Report_2023.pdf", "BMW", 2023, "BMW Finance N.V.", "EUR", BMW_FINANCE_NOTE),
    DocumentInfo("Ford/Ford_Annual_Report_2021.pdf", "Ford", 2021, "Ford Motor Company", "USD"),
    DocumentInfo("Ford/Ford_Annual_Report_2022.pdf", "Ford", 2022, "Ford Motor Company", "USD"),
    DocumentInfo("Ford/Ford_Annual_Report_2023.pdf", "Ford", 2023, "Ford Motor Company", "USD"),
    DocumentInfo("Tesla/Tesla_Annual_Report_2022.pdf", "Tesla", 2022, "Tesla, Inc.", "USD"),
    DocumentInfo("Tesla/Tesla_Annual_Report_2023.pdf", "Tesla", 2023, "Tesla, Inc.", "USD"),
    DocumentInfo("news.pdf", None, None, "news", None, "general news articles, not about any registered company"),
    DocumentInfo("news.docx", None, None, "news", None, "general news articles, not about any registered company"),
)

COMPANIES: tuple[str, ...] = tuple(sorted({d.company for d in REGISTRY if d.company}))

_BY_NAME = {d.name: d for d in REGISTRY}


def lookup(path: str | Path) -> DocumentInfo:
    """Registry entry for a file, matched by file name."""
    name = Path(path).name
    if name not in _BY_NAME:
        raise KeyError(f"{name} is not in the document registry")
    return _BY_NAME[name]


def registered_documents(data_dir: Path) -> list[tuple[DocumentInfo, Path]]:
    """Registry entries paired with their on-disk path, in registry order.

    Only registered files are ingested. A file without company, year and entity
    metadata could not be labelled or cited reliably, so unregistered files under
    the data directory are ignored rather than guessed at.
    """
    found = []
    for info in REGISTRY:
        path = data_dir / info.source_file
        if path.exists():
            found.append((info, path))
    return found


def missing_documents(data_dir: Path) -> list[DocumentInfo]:
    """Registry entries whose file is absent from the data directory."""
    return [info for info in REGISTRY if not (data_dir / info.source_file).exists()]
