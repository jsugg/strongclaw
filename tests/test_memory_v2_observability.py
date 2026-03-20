"""Observability tests for strongclaw memory v2."""

from __future__ import annotations

import json
import pathlib
import textwrap
from collections.abc import Sequence
from dataclasses import replace

import pytest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult

from clawops import observability
from clawops.memory_v2 import DenseSearchCandidate, MemoryV2Engine, load_config


def _write_memory_v2_config(workspace_root: pathlib.Path, config_path: pathlib.Path) -> None:
    config_path.write_text(
        textwrap.dedent("""
            storage:
              db_path: .openclaw/test-memory-v2.sqlite
            workspace:
              root: .
              include_default_memory: true
              memory_file_names:
                - MEMORY.md
                - memory.md
              daily_dir: memory
              bank_dir: bank
            corpus:
              paths:
                - name: docs
                  path: docs
                  pattern: "**/*.md"
            limits:
              max_snippet_chars: 240
              default_max_results: 6
            """).strip() + "\n",
        encoding="utf-8",
    )


def _build_workspace(tmp_path: pathlib.Path) -> pathlib.Path:
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "memory").mkdir(parents=True)
    (workspace / "bank").mkdir(parents=True)
    (workspace / "MEMORY.md").write_text(
        "# Project Memory\n\n- Fact: The deploy process uses blue/green cutovers.\n",
        encoding="utf-8",
    )
    (workspace / "docs" / "runbook.md").write_text(
        """
        # Gateway Runbook

        Rotate the gateway token before enabling a new browser profile.
        """.strip() + "\n",
        encoding="utf-8",
    )
    return workspace


class RecordingExporter(SpanExporter):
    """Collect spans for assertions."""

    def __init__(self) -> None:
        self.spans: list[ReadableSpan] = []

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None


class _FakeEmbeddingProvider:
    def __init__(self, vector: list[float]) -> None:
        self.vector = vector

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        return [list(self.vector) for _ in texts]


class _FakeQdrantBackend:
    def __init__(self) -> None:
        self.search_results: list[DenseSearchCandidate] = []
        self.raise_on_search = False

    def health(self) -> dict[str, object]:
        return {"enabled": True, "healthy": True, "collection": "test"}

    def ensure_collection(self, *, vector_size: int, include_sparse: bool = False) -> None:
        assert isinstance(include_sparse, bool)
        assert vector_size > 0

    def upsert_points(self, points: list[dict[str, object]]) -> None:
        assert points

    def delete_points(self, point_ids: list[str]) -> None:
        assert isinstance(point_ids, list)

    def search_dense(
        self, *, vector: list[float], limit: int, mode: str, scope: str | None
    ) -> list[DenseSearchCandidate]:
        del vector, limit, mode, scope
        if self.raise_on_search:
            raise RuntimeError("dense backend unavailable")
        return list(self.search_results)

    def search_sparse(
        self,
        *,
        vector: dict[str, list[int] | list[float]],
        limit: int,
        mode: str,
        scope: str | None,
    ) -> list[object]:
        del vector, limit, mode, scope
        return []

    def search(
        self, *, vector: list[float], limit: int, mode: str, scope: str | None
    ) -> list[DenseSearchCandidate]:
        return self.search_dense(vector=vector, limit=limit, mode=mode, scope=scope)


def _configure_engine(tmp_path: pathlib.Path) -> tuple[MemoryV2Engine, _FakeQdrantBackend]:
    workspace = _build_workspace(tmp_path)
    config_path = workspace / "memory-v2.yaml"
    _write_memory_v2_config(workspace, config_path)
    config = load_config(config_path)
    config = replace(
        config,
        backend=replace(config.backend, active="qdrant_dense_hybrid", fallback="sqlite_fts"),
        embedding=replace(
            config.embedding,
            enabled=True,
            provider="compatible-http",
            model="dense-test",
            base_url="http://127.0.0.1:9",
        ),
        qdrant=replace(config.qdrant, enabled=True, collection="memory-v2-observability"),
    )
    engine = MemoryV2Engine(config)
    engine._embedding_provider = _FakeEmbeddingProvider([1.0, 0.0, 0.0])
    fake_qdrant = _FakeQdrantBackend()
    engine._qdrant_backend = fake_qdrant
    return engine, fake_qdrant


def _configure_test_tracing(monkeypatch: pytest.MonkeyPatch) -> RecordingExporter:
    exporter = RecordingExporter()
    observability.reset_for_tests()
    monkeypatch.setenv("CLAWOPS_OTEL_ENABLED", "1")
    monkeypatch.setattr(observability, "_make_span_exporter", lambda: exporter)
    monkeypatch.setattr(
        observability,
        "_make_span_processor",
        lambda span_exporter: SimpleSpanProcessor(span_exporter),
    )
    return exporter


def test_memory_v2_emits_structured_logs_for_dense_search(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CLAWOPS_STRUCTURED_LOGS", "1")
    engine, fake_qdrant = _configure_engine(tmp_path)
    engine.reindex()

    with engine.connect() as conn:
        row = conn.execute(
            "SELECT id FROM search_items WHERE rel_path = ? AND lane = 'corpus' LIMIT 1",
            ("docs/runbook.md",),
        ).fetchone()
    assert row is not None
    fake_qdrant.search_results = [
        DenseSearchCandidate(item_id=int(row["id"]), point_id="runbook-1", score=0.93)
    ]

    hits = engine.search("credential rollover checklist", lane="all")
    stderr_lines = [
        json.loads(line) for line in capsys.readouterr().err.splitlines() if line.strip()
    ]

    assert hits
    event_names = {record["event"] for record in stderr_lines}
    assert "clawops.memory_v2.embedding" in event_names
    assert "clawops.memory_v2.qdrant.search.dense" in event_names
    assert "clawops.memory_v2.search" in event_names
    assert "clawops.memory_v2.vector_sync" in event_names


def test_memory_v2_search_exports_trace_spans(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = _configure_test_tracing(monkeypatch)
    engine, fake_qdrant = _configure_engine(tmp_path)
    engine.reindex()

    with engine.connect() as conn:
        row = conn.execute(
            "SELECT id FROM search_items WHERE rel_path = ? AND lane = 'corpus' LIMIT 1",
            ("docs/runbook.md",),
        ).fetchone()
    assert row is not None
    fake_qdrant.search_results = [
        DenseSearchCandidate(item_id=int(row["id"]), point_id="runbook-1", score=0.93)
    ]

    engine.search("credential rollover checklist", lane="all")
    observability.force_flush()

    span_names = {span.name for span in exporter.spans}
    assert "clawops.memory_v2.reindex" in span_names
    assert "clawops.memory_v2.vector_sync" in span_names
    assert "clawops.memory_v2.search" in span_names
    assert "clawops.memory_v2.qdrant.search.dense" in span_names
    search_span = next(span for span in exporter.spans if span.name == "clawops.memory_v2.search")
    assert search_span.attributes["resolvedBackend"] == "qdrant_dense_hybrid"
    assert search_span.attributes["results"] >= 1


def test_memory_v2_logs_fallback_activation(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CLAWOPS_STRUCTURED_LOGS", "1")
    engine, fake_qdrant = _configure_engine(tmp_path)
    fake_qdrant.raise_on_search = True
    engine.reindex()

    hits = engine.search("gateway token", lane="all")
    stderr_lines = [
        json.loads(line) for line in capsys.readouterr().err.splitlines() if line.strip()
    ]

    assert hits
    assert hits[0].backend == "sqlite_fts"
    fallback_log = next(
        record for record in stderr_lines if record["event"] == "clawops.memory_v2.search.fallback"
    )
    assert fallback_log["resolvedBackend"] == "sqlite_fts"
