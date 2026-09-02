"""Build and persist the vector index: parse, chunk, embed, store.

The FAISS index and a parallel JSON list of chunks are written to the index
directory together with a manifest of file hashes, so the app can tell whether
the stored index still matches the corpus and skip re-embedding when it does.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable

import faiss

from src.config import DATA_DIR, EMBEDDING_MODEL, INDEX_DIR
from src.ingest.chunk import Chunk, chunk_document
from src.ingest.parse import parse_document
from src.ingest.registry import missing_documents, registered_documents
from src.llm import LLM

INDEX_FILE = "index.faiss"
CHUNKS_FILE = "chunks.json"
MANIFEST_FILE = "manifest.json"
PIPELINE_VERSION = 4

Progress = Callable[[str], None]


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def corpus_manifest(data_dir: Path) -> dict:
    return {
        "embedding_model": EMBEDDING_MODEL,
        "pipeline_version": PIPELINE_VERSION,
        "files": {info.name: _sha1(path) for info, path in registered_documents(data_dir)},
    }


def index_is_current(data_dir: Path = DATA_DIR, index_dir: Path = INDEX_DIR) -> bool:
    manifest_path = index_dir / MANIFEST_FILE
    if not all((index_dir / name).exists() for name in (INDEX_FILE, CHUNKS_FILE, MANIFEST_FILE)):
        return False
    stored = json.loads(manifest_path.read_text(encoding="utf-8"))
    return stored == corpus_manifest(data_dir)


def load_chunks(index_dir: Path = INDEX_DIR) -> list[Chunk]:
    data = json.loads((index_dir / CHUNKS_FILE).read_text(encoding="utf-8"))
    return [Chunk.from_dict(item) for item in data]


def build_index(
    llm: LLM,
    data_dir: Path = DATA_DIR,
    index_dir: Path = INDEX_DIR,
    progress: Progress = print,
) -> list[Chunk]:
    documents = registered_documents(data_dir)
    if not documents:
        raise RuntimeError(f"No registered documents found under {data_dir}")
    for info in missing_documents(data_dir):
        progress(f"  missing: {info.source_file} (skipped)")

    chunks: list[Chunk] = []
    for info, path in documents:
        units = parse_document(path)
        doc_chunks = chunk_document(info, units)
        chunks.extend(doc_chunks)
        pages = {u.page for u in units if u.page is not None}
        deduped = {u.page for u in units if u.dedup_applied}
        tables = sum(1 for c in doc_chunks if c.chunk_type == "table")
        rows = sum(1 for c in doc_chunks if c.chunk_type == "row")
        where = f"{len(pages)} pages" if pages else f"{len(units)} paragraphs"
        note = f", {len(deduped)} pages de-duplicated" if deduped else ""
        progress(
            f"  {info.name}: {where}{note}, {len(doc_chunks) - rows} chunks ({tables} tables), {rows} table rows"
        )

    progress(f"  embedding {len(chunks)} chunks with {EMBEDDING_MODEL} ...")
    vectors = llm.embed([c.embed_text for c in chunks])
    index = faiss.IndexFlatIP(vectors.shape[1])
    index.add(vectors)

    index_dir.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(index_dir / INDEX_FILE))
    (index_dir / CHUNKS_FILE).write_text(
        json.dumps([c.to_dict() for c in chunks], ensure_ascii=False), encoding="utf-8"
    )
    (index_dir / MANIFEST_FILE).write_text(json.dumps(corpus_manifest(data_dir), indent=2), encoding="utf-8")
    progress(f"  index written to {index_dir}")
    return chunks


def ensure_index(llm: LLM, data_dir: Path = DATA_DIR, index_dir: Path = INDEX_DIR, progress: Progress = print) -> None:
    if index_is_current(data_dir, index_dir):
        return
    progress("Building the index (first run, or the corpus changed) ...")
    build_index(llm, data_dir, index_dir, progress)


if __name__ == "__main__":
    build_index(LLM())
