"""Deterministic sparse-vector encoding for hybrid hypermemory retrieval."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence

from clawops.hypermemory.models import normalize_text_tokens

BM25_K1 = 1.2
BM25_B = 0.75
SPARSE_ENCODER_VERSION = "bm25-v1"


@dataclasses.dataclass(frozen=True, slots=True)
class SparseVector:
    """Sparse vector payload compatible with the Qdrant REST API."""

    indices: tuple[int, ...]
    values: tuple[float, ...]

    @property
    def is_empty(self) -> bool:
        """Return whether the sparse vector contains any weighted terms."""
        return not self.indices

    def to_qdrant(self) -> dict[str, list[int] | list[float]]:
        """Return the vector payload expected by Qdrant."""
        return {"indices": list(self.indices), "values": list(self.values)}


@dataclasses.dataclass(frozen=True, slots=True)
class PreparedSparseDocument:
    """One retrieval document with reusable normalized sparse tokens."""

    text: str
    tokens: tuple[str, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class SparseEncoder:
    """Corpus-aware sparse encoder with deterministic term IDs."""

    term_to_id: Mapping[str, int]
    document_frequency: Mapping[str, int]
    document_count: int
    average_document_length: float
    fingerprint: str

    @property
    def vocabulary_size(self) -> int:
        """Return the number of indexed sparse terms."""
        return len(self.term_to_id)

    def encode_document(self, text: str) -> SparseVector:
        """Encode indexed content using BM25-style document weights."""
        return self.encode_document_tokens(normalize_text_tokens(text))

    def encode_query(self, text: str) -> SparseVector:
        """Encode a query using the persisted sparse vocabulary."""
        return self.encode_query_tokens(normalize_text_tokens(text))

    def encode_document_tokens(self, tokens: Sequence[str]) -> SparseVector:
        """Encode indexed content from an already-normalized token stream."""
        return self._encode_tokens(tokens, is_document=True)

    def encode_query_tokens(self, tokens: Sequence[str]) -> SparseVector:
        """Encode a query from an already-normalized token stream."""
        return self._encode_tokens(tokens, is_document=False)

    def _encode_tokens(self, tokens: Sequence[str], *, is_document: bool) -> SparseVector:
        if not tokens or self.document_count <= 0 or not self.term_to_id:
            return SparseVector(indices=(), values=())
        counts = Counter(token for token in tokens if token in self.term_to_id)
        if not counts:
            return SparseVector(indices=(), values=())
        doc_length = max(len(tokens), 1)
        denom_base = 1.0
        if is_document and self.average_document_length > 0:
            denom_base = 1.0 - BM25_B + BM25_B * (doc_length / self.average_document_length)
        pairs: list[tuple[int, float]] = []
        for term, term_id in sorted(self.term_to_id.items(), key=lambda item: item[1]):
            frequency = counts.get(term, 0)
            if frequency <= 0:
                continue
            weight = (
                self._document_weight(term, frequency, denom_base)
                if is_document
                else self._query_weight(term, frequency)
            )
            if weight <= 0.0:
                continue
            pairs.append((term_id, weight))
        if not pairs:
            return SparseVector(indices=(), values=())
        indices, values = zip(*pairs, strict=True)
        return SparseVector(indices=tuple(indices), values=tuple(values))

    def _document_weight(self, term: str, frequency: int, denom_base: float) -> float:
        idf = self._idf(term)
        numerator = frequency * (BM25_K1 + 1.0)
        denominator = frequency + (BM25_K1 * denom_base)
        if denominator <= 0.0:
            return 0.0
        return idf * (numerator / denominator)

    def _query_weight(self, term: str, frequency: int) -> float:
        idf = self._idf(term)
        return idf * (1.0 + math.log(float(frequency)))

    def _idf(self, term: str) -> float:
        doc_frequency = self.document_frequency.get(term, 0)
        if doc_frequency <= 0 or self.document_count <= 0:
            return 0.0
        numerator = self.document_count - doc_frequency + 0.5
        denominator = doc_frequency + 0.5
        return math.log1p(max(numerator / denominator, 0.0))


def build_sparse_encoder(texts: Sequence[str]) -> SparseEncoder:
    """Build a deterministic sparse encoder from normalized retrieval texts."""
    return build_sparse_encoder_from_documents([prepare_sparse_document(text) for text in texts])


def prepare_sparse_document(text: str) -> PreparedSparseDocument:
    """Normalize *text* once for sparse encoder build and document encoding."""
    return PreparedSparseDocument(text=text, tokens=tuple(normalize_text_tokens(text)))


def build_sparse_encoder_from_documents(
    documents: Sequence[PreparedSparseDocument],
) -> SparseEncoder:
    """Build a deterministic sparse encoder from prepared sparse documents."""
    document_frequency: Counter[str] = Counter()
    total_terms = 0
    document_count = 0
    for document in documents:
        tokens = document.tokens
        if not tokens:
            continue
        document_count += 1
        total_terms += len(tokens)
        document_frequency.update(set(tokens))
    average_document_length = (total_terms / document_count) if document_count > 0 else 0.0
    ordered_terms = sorted(document_frequency)
    term_to_id = {term: index for index, term in enumerate(ordered_terms)}
    fingerprint_payload = {
        "version": SPARSE_ENCODER_VERSION,
        "documentCount": document_count,
        "averageDocumentLength": round(average_document_length, 8),
        "documentFrequency": [[term, document_frequency[term]] for term in ordered_terms],
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    return SparseEncoder(
        term_to_id=term_to_id,
        document_frequency=dict(document_frequency),
        document_count=document_count,
        average_document_length=average_document_length,
        fingerprint=fingerprint,
    )
