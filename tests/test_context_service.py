"""Unit tests for the lexical context service."""

from __future__ import annotations

import pathlib
import sqlite3

import pytest

from clawops.common import write_yaml
from clawops.context_service import load_config, service_from_config


def test_index_and_query(tmp_path: pathlib.Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "auth.py").write_text(
        "class AuthService:\n    def validate_jwt(self):\n        return True\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "context.yaml"
    write_yaml(config_path, {"index": {"db_path": ".clawops/context.sqlite"}})
    service = service_from_config(config_path, repo)
    count = service.index()
    assert count == 1
    hits = service.query("validate_jwt", limit=3)
    assert hits
    assert hits[0].path == "auth.py"
    assert hits[0].start_line == 1
    assert hits[0].end_line >= hits[0].start_line
    assert "2:     def validate_jwt(self):" in hits[0].snippet


def test_context_service_respects_include_and_exclude_globs(tmp_path: pathlib.Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "keep.py").write_text("def keep_me():\n    return True\n", encoding="utf-8")
    (repo / "ignore.py").write_text("def ignore_me():\n    return True\n", encoding="utf-8")
    docs = repo / "docs"
    docs.mkdir()
    (docs / "note.md").write_text("# keep\n", encoding="utf-8")
    config_path = tmp_path / "context.yaml"
    write_yaml(
        config_path,
        {
            "index": {"db_path": ".clawops/context.sqlite"},
            "paths": {
                "include": ["**/*.py", "**/*.md"],
                "exclude": ["ignore.py"],
            },
        },
    )
    service = service_from_config(config_path, repo)
    count = service.index()
    assert count == 2
    hits = service.query("ignore_me", limit=3)
    assert hits == []


def test_reindex_prunes_deleted_files(tmp_path: pathlib.Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "auth.py"
    target.write_text("def validate_jwt():\n    return True\n", encoding="utf-8")
    config_path = tmp_path / "context.yaml"
    write_yaml(config_path, {"index": {"db_path": ".clawops/context.sqlite"}})
    service = service_from_config(config_path, repo)
    assert service.index() == 1
    assert service.query("validate_jwt", limit=3)

    target.unlink()
    assert service.index() == 0
    assert service.query("validate_jwt", limit=3) == []


def test_reindex_prunes_newly_excluded_files_from_search_and_packs(tmp_path: pathlib.Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "keep.py").write_text("def keep_me():\n    return True\n", encoding="utf-8")
    (repo / "ignore.py").write_text("def ignore_me():\n    return True\n", encoding="utf-8")
    config_path = tmp_path / "context.yaml"
    write_yaml(
        config_path,
        {
            "index": {"db_path": ".clawops/context.sqlite"},
            "paths": {"include": ["**/*.py"]},
        },
    )

    initial_service = service_from_config(config_path, repo)
    assert initial_service.index() == 2
    assert initial_service.query("ignore_me", limit=3)

    write_yaml(
        config_path,
        {
            "index": {"db_path": ".clawops/context.sqlite"},
            "paths": {
                "include": ["**/*.py"],
                "exclude": ["ignore.py"],
            },
        },
    )

    filtered_service = service_from_config(config_path, repo)
    assert filtered_service.index() == 1
    keep_hits = filtered_service.query("keep_me", limit=3)
    keep_pack = filtered_service.pack("keep_me", limit=3)
    ignore_pack = filtered_service.pack("ignore_me", limit=3)
    assert filtered_service.query("ignore_me", limit=3) == []
    assert [hit.path for hit in keep_hits] == ["keep.py"]
    assert "keep.py" in keep_pack
    assert "ignore.py" not in keep_pack
    assert "ignore.py" not in ignore_pack
    assert "keep.py" not in ignore_pack

    with filtered_service.connect() as conn:
        file_paths = [
            str(row["path"])
            for row in conn.execute("SELECT path FROM files ORDER BY path").fetchall()
        ]

    assert file_paths == ["keep.py"]


def test_context_service_skips_symlinks_that_escape_repo_root(tmp_path: pathlib.Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside-secret.md"
    outside.write_text("TOPSECRET=1\n", encoding="utf-8")
    leak_path = repo / "leak.md"
    leak_path.symlink_to(pathlib.Path("..") / outside.name)
    config_path = tmp_path / "context.yaml"
    write_yaml(
        config_path,
        {
            "index": {
                "db_path": ".clawops/context.sqlite",
                "symlink_policy": "in_repo_only",
            },
            "paths": {"include": ["**/*.md"]},
        },
    )

    service = service_from_config(config_path, repo)
    assert service.index() == 0
    assert service.query("TOPSECRET", limit=3) == []
    assert "TOPSECRET=1" not in service.pack("TOPSECRET", limit=3)
    leak_path.unlink()


def test_context_service_allows_in_repo_symlinks_by_default(tmp_path: pathlib.Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    docs = repo / "docs"
    docs.mkdir()
    (docs / "source.md").write_text("INTERNAL_ONLY=1\n", encoding="utf-8")
    alias_path = repo / "alias.md"
    alias_path.symlink_to(pathlib.Path("docs") / "source.md")
    config_path = tmp_path / "context.yaml"
    write_yaml(
        config_path,
        {
            "index": {"db_path": ".clawops/context.sqlite"},
            "paths": {"include": ["**/*.md"]},
        },
    )

    service = service_from_config(config_path, repo)
    assert service.index() == 2
    hits = service.query("INTERNAL_ONLY", limit=4)
    assert {hit.path for hit in hits} == {"alias.md", "docs/source.md"}
    alias_path.unlink()


def test_context_config_rejects_unknown_symlink_policy(tmp_path: pathlib.Path) -> None:
    config_path = tmp_path / "context.yaml"
    write_yaml(
        config_path,
        {
            "index": {
                "db_path": ".clawops/context.sqlite",
                "symlink_policy": "unsafe",
            }
        },
    )

    with pytest.raises(ValueError, match="index.symlink_policy must be one of"):
        load_config(config_path)


def test_context_config_rejects_invalid_scalar_types(tmp_path: pathlib.Path) -> None:
    config_path = tmp_path / "context.yaml"
    write_yaml(
        config_path,
        {
            "index": {
                "db_path": ".clawops/context.sqlite",
                "include_hidden": "false",
            }
        },
    )

    with pytest.raises(TypeError, match="index.include_hidden must be a boolean"):
        load_config(config_path)


def test_context_config_rejects_non_positive_max_file_size(tmp_path: pathlib.Path) -> None:
    config_path = tmp_path / "context.yaml"
    write_yaml(
        config_path,
        {
            "index": {
                "db_path": ".clawops/context.sqlite",
                "max_file_size_bytes": -1,
            }
        },
    )

    with pytest.raises(ValueError, match="index.max_file_size_bytes must be positive"):
        load_config(config_path)


def test_index_with_stats_skips_unchanged_files(tmp_path: pathlib.Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "auth.py"
    target.write_text("def validate_jwt():\n    return True\n", encoding="utf-8")
    config_path = tmp_path / "context.yaml"
    write_yaml(config_path, {"index": {"db_path": ".clawops/context.sqlite"}})

    service = service_from_config(config_path, repo)
    first = service.index_with_stats()
    second = service.index_with_stats()

    assert first.total_files == 1
    assert first.indexed_files == 1
    assert first.skipped_files == 0
    assert second.total_files == 1
    assert second.indexed_files == 0
    assert second.skipped_files == 1
    assert second.deleted_files == 0


def test_index_with_stats_preloads_metadata_once_for_unchanged_repos(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    for index in range(3):
        (repo / f"file_{index}.py").write_text(
            f"def fn_{index}():\n    return {index}\n",
            encoding="utf-8",
        )
    config_path = tmp_path / "context.yaml"
    write_yaml(config_path, {"index": {"db_path": ".clawops/context.sqlite"}})

    service = service_from_config(config_path, repo)
    first = service.index_with_stats()
    statements: list[str] = []
    original_connect = service.connect

    def _connect_with_trace() -> sqlite3.Connection:
        conn = original_connect()
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(service, "connect", _connect_with_trace)
    second = service.index_with_stats()

    assert first.indexed_files == 3
    assert second.indexed_files == 0
    assert second.skipped_files == 3
    assert any(
        "SELECT path, mtime_ns, size_bytes FROM files" in statement for statement in statements
    )
    assert not any(
        "SELECT mtime_ns, size_bytes FROM files WHERE path =" in statement
        for statement in statements
    )


def test_index_with_stats_reports_deleted_files_after_preloaded_metadata(
    tmp_path: pathlib.Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    keep = repo / "keep.py"
    remove = repo / "remove.py"
    keep.write_text("def keep():\n    return True\n", encoding="utf-8")
    remove.write_text("def remove_me():\n    return True\n", encoding="utf-8")
    config_path = tmp_path / "context.yaml"
    write_yaml(config_path, {"index": {"db_path": ".clawops/context.sqlite"}})

    service = service_from_config(config_path, repo)
    initial = service.index_with_stats()
    remove.unlink()
    second = service.index_with_stats()

    assert initial.indexed_files == 2
    assert second.total_files == 1
    assert second.indexed_files == 0
    assert second.skipped_files == 1
    assert second.deleted_files == 1
    assert service.query("remove_me", limit=2) == []
