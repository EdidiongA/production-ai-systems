"""Paragraph-level chunker.

Design decision (defended in README): chunks are single paragraphs from the
source markdown, not fixed-token windows. The corpus is technical reference
material where each paragraph is a self-contained claim cluster; fixed windows
split numeric claims from their qualifiers, which is the #1 cause of citation
mismatch we observed in early testing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Chunk:
    chunk_id: str      # e.g. "03_lifepo4_batteries#p2"
    doc_id: str        # e.g. "03_lifepo4_batteries"
    title: str         # document H1
    text: str
    position: int      # paragraph index within the document


def load_corpus(docs_dir: str | Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(Path(docs_dir).glob("*.md")):
        doc_id = path.stem
        raw = path.read_text(encoding="utf-8")
        title_match = re.search(r"^#\s+(.+)$", raw, flags=re.MULTILINE)
        title = title_match.group(1).strip() if title_match else doc_id
        body = re.sub(r"^#\s+.+$", "", raw, count=1, flags=re.MULTILINE)
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
        for i, para in enumerate(paragraphs):
            chunks.append(
                Chunk(
                    chunk_id=f"{doc_id}#p{i}",
                    doc_id=doc_id,
                    title=title,
                    text=para,
                    position=i,
                )
            )
    return chunks
