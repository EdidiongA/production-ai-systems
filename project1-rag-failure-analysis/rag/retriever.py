"""BM25 retriever (pure Python, no external index).

Design decision (defended in README): BM25 over a vector DB. The corpus is
small (<100 chunks), heavily numeric, and full of exact technical tokens
("LiFePO4", "55.2 V", "Class T fuse") where lexical match outperforms generic
embeddings without domain fine-tuning. BM25 is also fully deterministic, which
makes the failure analysis reproducible. The Retriever interface is swappable:
drop in an EmbeddingRetriever with the same .search() signature to A/B test.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from .chunker import Chunk

_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "of", "for",
    "and", "or", "to", "in", "on", "at", "by", "with", "from", "as", "that",
    "this", "it", "its", "what", "which", "how", "why", "when", "do", "does",
    "should", "can", "my", "i", "you", "your", "much", "many",
}


def tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", text.lower())
    return [t for t in tokens if t not in _STOPWORDS]


@dataclass
class ScoredChunk:
    chunk: Chunk
    score: float


class BM25Retriever:
    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.k1, self.b = k1, b
        self._doc_tokens = [tokenize(c.text + " " + c.title) for c in chunks]
        self._doc_lens = [len(t) for t in self._doc_tokens]
        self._avgdl = sum(self._doc_lens) / max(len(self._doc_lens), 1)
        self._tfs = [Counter(t) for t in self._doc_tokens]
        df: Counter = Counter()
        for tokens in self._doc_tokens:
            df.update(set(tokens))
        n = len(chunks)
        self._idf = {
            term: math.log(1 + (n - f + 0.5) / (f + 0.5)) for term, f in df.items()
        }

    def search(self, query: str, top_k: int = 4) -> list[ScoredChunk]:
        q_tokens = tokenize(query)
        scores: list[float] = []
        for i in range(len(self.chunks)):
            s = 0.0
            for term in q_tokens:
                if term not in self._tfs[i]:
                    continue
                tf = self._tfs[i][term]
                idf = self._idf.get(term, 0.0)
                denom = tf + self.k1 * (
                    1 - self.b + self.b * self._doc_lens[i] / self._avgdl
                )
                s += idf * tf * (self.k1 + 1) / denom
            scores.append(s)
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return [
            ScoredChunk(self.chunks[i], round(scores[i], 4))
            for i in ranked[:top_k]
            if scores[i] > 0
        ]
