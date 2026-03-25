"""Core engine for StrongClaw hypermemory."""

from __future__ import annotations

import sqlite3

from clawops.common import ensure_parent
from clawops.hypermemory.config import HypermemoryConfig
from clawops.hypermemory.engine_backend import (
    _backend_fingerprint,
    _backend_state_value,
    _backend_uses_qdrant,
    _backend_uses_sparse_vectors,
    _canonical_backend,
    _current_sparse_fingerprint,
    _dense_search,
    _embed_texts,
    _embedding_batches,
    _load_sparse_encoder,
    _normalize_text,
    _normalized_retrieval_text,
    _point_id,
    _sha256,
    _slugify,
    _sparse_encoder_for_documents,
    _sparse_fingerprint_for_documents,
    _sparse_search,
    _sync_dense_backend,
    _vector_rows_for_documents,
    _write_backend_state,
    _write_sparse_state,
)
from clawops.hypermemory.engine_indexing import (
    _clear_derived_rows,
    _count_rows,
    _count_sparse_vector_items,
    _evidence_entries,
    _insert_typed_row,
    _iter_corpus_documents,
    _iter_documents,
    _iter_memory_documents,
    _missing_corpus_paths,
    _missing_required_corpus_paths,
    _rebuild_fact_registry,
    reindex,
)
from clawops.hypermemory.engine_memory import (
    _age_days,
    _allows_memory_pro_export_path,
    _append_unique_entry,
    _apply_forget,
    _build_proposal,
    _document_header,
    _entry_hash_prefix,
    _entry_identity,
    _entry_reference_from_item_id,
    _entry_reference_from_text,
    _fact_category,
    _format_entry_line,
    _format_proposal_line,
    _increment_feedback_counts,
    _infer_fact_key,
    _infer_query_fact_key,
    _invalidated_line,
    _is_noise,
    _is_semantically_duplicate,
    _load_entities_json,
    _load_evidence_json,
    _memory_pro_export_rows,
    _memory_pro_importance,
    _memory_pro_timestamp_ms,
    _normalize_tier,
    _passes_admission,
    _proposal_kind,
    _resolve_entry_reference,
    _resolve_read_path,
    _resolve_writable_path,
    _search_hit_text,
    _store_target,
    _synced_line_from_row,
    _typed_entry_text,
    benchmark_cases,
    capture,
    export_memory_pro_import,
    flush_metadata,
    forget,
    get_fact,
    list_facts,
    record_access,
    record_bad_recall,
    record_confirmation,
    record_injection,
    reflect,
    run_lifecycle,
    store,
    supersede,
    update,
)
from clawops.hypermemory.engine_query import (
    _collection_has_hypermemory_vector_lanes,
    _exact_fact_lookup,
    _filter_current_fact_hits,
    _hypermemory_probe_query,
    _observed_rerank_scorer,
    _rerank_probe_documents,
    _rerank_resolved_device,
    _row_to_search_hit,
    _search_invalidated_hits,
    _verify_rerank_provider,
    is_dirty,
    read,
    search,
    status,
    verify,
)
from clawops.hypermemory.providers import create_embedding_provider, create_rerank_provider
from clawops.hypermemory.qdrant_backend import QdrantBackend
from clawops.hypermemory.schema import ensure_schema


class HypermemoryEngine:
    """Markdown-canonical memory engine with a derived SQLite index."""

    def __init__(self, config: HypermemoryConfig) -> None:
        self.config = config
        self._embedding_provider = create_embedding_provider(config.embedding)
        self._rerank_provider = create_rerank_provider(config.rerank)
        self._qdrant_backend = QdrantBackend(config.qdrant)

    def connect(self) -> sqlite3.Connection:
        """Open a configured SQLite connection."""
        ensure_parent(self.config.db_path)
        conn = sqlite3.connect(self.config.db_path)
        conn.row_factory = sqlite3.Row
        ensure_schema(conn)
        return conn

    status = status
    is_dirty = is_dirty
    reindex = reindex
    search = search
    _observed_rerank_scorer = _observed_rerank_scorer
    _rerank_resolved_device = _rerank_resolved_device
    _rerank_probe_documents = _rerank_probe_documents
    _verify_rerank_provider = _verify_rerank_provider
    verify = verify
    read = read
    export_memory_pro_import = export_memory_pro_import
    store = store
    update = update
    reflect = reflect
    capture = capture
    forget = forget
    supersede = supersede
    record_access = record_access
    record_injection = record_injection
    record_confirmation = record_confirmation
    record_bad_recall = record_bad_recall
    flush_metadata = flush_metadata
    run_lifecycle = run_lifecycle
    get_fact = get_fact
    list_facts = list_facts
    benchmark_cases = benchmark_cases
    _is_noise = _is_noise
    _passes_admission = _passes_admission
    _normalize_tier = _normalize_tier
    _infer_fact_key = _infer_fact_key
    _infer_query_fact_key = _infer_query_fact_key
    _fact_category = _fact_category
    _entry_hash_prefix = _entry_hash_prefix
    _search_hit_text = _search_hit_text
    _typed_entry_text = _typed_entry_text
    _resolve_entry_reference = _resolve_entry_reference
    _entry_reference_from_item_id = _entry_reference_from_item_id
    _entry_reference_from_text = _entry_reference_from_text
    _apply_forget = _apply_forget
    _invalidated_line = _invalidated_line
    _synced_line_from_row = _synced_line_from_row
    _row_to_search_hit = _row_to_search_hit
    _rebuild_fact_registry = _rebuild_fact_registry
    _exact_fact_lookup = _exact_fact_lookup
    _filter_current_fact_hits = _filter_current_fact_hits
    _search_invalidated_hits = _search_invalidated_hits
    _is_semantically_duplicate = _is_semantically_duplicate
    _increment_feedback_counts = _increment_feedback_counts
    _age_days = _age_days
    _memory_pro_export_rows = _memory_pro_export_rows
    _allows_memory_pro_export_path = _allows_memory_pro_export_path
    _load_entities_json = _load_entities_json
    _load_evidence_json = _load_evidence_json
    _memory_pro_importance = _memory_pro_importance
    _memory_pro_timestamp_ms = _memory_pro_timestamp_ms
    _iter_documents = _iter_documents
    _iter_memory_documents = _iter_memory_documents
    _iter_corpus_documents = _iter_corpus_documents
    _missing_corpus_paths = _missing_corpus_paths
    _missing_required_corpus_paths = _missing_required_corpus_paths
    _clear_derived_rows = _clear_derived_rows
    _insert_typed_row = _insert_typed_row
    _evidence_entries = _evidence_entries
    _resolve_read_path = _resolve_read_path
    _resolve_writable_path = _resolve_writable_path
    _store_target = _store_target
    _format_entry_line = _format_entry_line
    _append_unique_entry = _append_unique_entry
    _document_header = _document_header
    _entry_identity = _entry_identity
    _build_proposal = _build_proposal
    _format_proposal_line = _format_proposal_line
    _proposal_kind = _proposal_kind
    _dense_search = _dense_search
    _sparse_search = _sparse_search
    _sync_dense_backend = _sync_dense_backend
    _embed_texts = _embed_texts
    _embedding_batches = _embedding_batches
    _count_sparse_vector_items = _count_sparse_vector_items
    _canonical_backend = _canonical_backend
    _backend_uses_qdrant = _backend_uses_qdrant
    _backend_uses_sparse_vectors = _backend_uses_sparse_vectors
    _vector_rows_for_documents = _vector_rows_for_documents
    _sparse_encoder_for_documents = _sparse_encoder_for_documents
    _sparse_fingerprint_for_documents = _sparse_fingerprint_for_documents
    _current_sparse_fingerprint = _current_sparse_fingerprint
    _write_sparse_state = _write_sparse_state
    _load_sparse_encoder = _load_sparse_encoder
    _collection_has_hypermemory_vector_lanes = _collection_has_hypermemory_vector_lanes
    _hypermemory_probe_query = _hypermemory_probe_query
    _backend_fingerprint = _backend_fingerprint
    _backend_state_value = _backend_state_value
    _write_backend_state = _write_backend_state
    _count_rows = _count_rows
    _normalized_retrieval_text = _normalized_retrieval_text
    _normalize_text = _normalize_text
    _point_id = _point_id
    _slugify = _slugify
    _sha256 = _sha256
