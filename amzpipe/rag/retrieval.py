"""Chunking + hybrid (dense + lexical) vector retrieval.

Dense embeddings use sentence-transformers when installed; otherwise the
index falls back to TF-IDF only, so everything runs offline with no GPU.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

log = logging.getLogger(__name__)
_ST_MODELS: dict = {}


@dataclass
class Chunk:
    id: str
    text: str
    section: str
    meta: dict = field(default_factory=dict)


def chunk_sections(sections: dict[str, str], size: int = 400, overlap: int = 60) -> list[Chunk]:
    """Split each page section into chunks, preserving line boundaries where possible."""
    chunks: list[Chunk] = []
    for sec, text in sections.items():
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        buf = ""
        n = 0
        for line in lines:
            while len(line) > size:  # very long single line
                head, line = line[:size], line[size - overlap:]
                if buf:
                    chunks.append(Chunk(f"{sec}-{n}", buf, sec)); n += 1; buf = ""
                chunks.append(Chunk(f"{sec}-{n}", head, sec)); n += 1
            if len(buf) + len(line) + 1 > size and buf:
                chunks.append(Chunk(f"{sec}-{n}", buf, sec)); n += 1
                buf = buf[-overlap:] + "\n" + line if overlap else line
            else:
                buf = f"{buf}\n{line}" if buf else line
        if buf:
            chunks.append(Chunk(f"{sec}-{n}", buf, sec))
    return chunks


def load_dense_model(cfg: dict):
    """Return a sentence-transformers model or None (-> TF-IDF only)."""
    choice = cfg.get("embedder", "auto")
    if choice == "tfidf":
        return None
    name = cfg.get("model", "sentence-transformers/all-MiniLM-L6-v2")
    if name in _ST_MODELS:
        return _ST_MODELS[name]
    try:
        from sentence_transformers import SentenceTransformer
        _ST_MODELS[name] = SentenceTransformer(name)
        log.info("Dense embedder loaded: %s", name)
    except Exception as e:  # not installed / no network
        if choice == "sentence-transformers":
            raise
        log.info("sentence-transformers unavailable (%s) — using TF-IDF retrieval", type(e).__name__)
        _ST_MODELS[name] = None
    return _ST_MODELS[name]


class VectorIndex:
    """Small in-memory hybrid index. score = alpha*dense + (1-alpha)*tfidf."""

    def __init__(self, dense_model=None, alpha: float = 0.65):
        self.dense = dense_model
        self.alpha = alpha if dense_model is not None else 0.0
        self.chunks: list[Chunk] = []
        self._tfidf = None
        self._tf_mat = None
        self._dense_mat = None

    def build(self, chunks: list[Chunk]) -> "VectorIndex":
        self.chunks = chunks
        if not chunks:
            return self
        texts = [c.text for c in chunks]
        self._tfidf = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, lowercase=True,
                                      token_pattern=r"(?u)\b\w+\b")
        self._tf_mat = self._tfidf.fit_transform(texts)
        if self.dense is not None:
            self._dense_mat = np.asarray(self.dense.encode(texts, normalize_embeddings=True))
        return self

    def search(self, query: str, k: int = 4) -> list[tuple[Chunk, float]]:
        if not self.chunks:
            return []
        q = self._tfidf.transform([query])
        lex = (self._tf_mat @ q.T).toarray().ravel()
        scores = lex
        if self._dense_mat is not None:
            qd = np.asarray(self.dense.encode([query], normalize_embeddings=True))[0]
            scores = self.alpha * (self._dense_mat @ qd) + (1 - self.alpha) * lex
        order = np.argsort(-scores)[:k]
        return [(self.chunks[i], float(scores[i])) for i in order]
