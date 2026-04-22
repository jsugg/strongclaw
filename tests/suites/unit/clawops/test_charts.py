"""Unit tests for charts.py."""

from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest

from clawops.charts import load_results, main, parse_args

# ---------------------------------------------------------------------------
# load_results
# ---------------------------------------------------------------------------


def test_load_results_valid_jsonl(tmp_path: pathlib.Path) -> None:
    jsonl = tmp_path / "results.jsonl"
    jsonl.write_text(
        json.dumps({"id": "tc-1", "passed": True})
        + "\n"
        + json.dumps({"id": "tc-2", "passed": False})
        + "\n",
        encoding="utf-8",
    )
    labels, values = load_results(jsonl)
    assert labels == ["tc-1", "tc-2"]
    assert values == [1, 0]


def test_load_results_skips_blank_lines(tmp_path: pathlib.Path) -> None:
    jsonl = tmp_path / "results.jsonl"
    jsonl.write_text(
        json.dumps({"id": "tc-1", "passed": True})
        + "\n\n"
        + json.dumps({"id": "tc-2", "passed": True})
        + "\n",
        encoding="utf-8",
    )
    labels, values = load_results(jsonl)
    assert len(labels) == 2
    assert len(values) == 2


def test_load_results_empty_file(tmp_path: pathlib.Path) -> None:
    jsonl = tmp_path / "empty.jsonl"
    jsonl.write_text("", encoding="utf-8")
    labels, values = load_results(jsonl)
    assert labels == []
    assert values == []


def test_load_results_single_entry(tmp_path: pathlib.Path) -> None:
    jsonl = tmp_path / "single.jsonl"
    jsonl.write_text(json.dumps({"id": "only", "passed": False}) + "\n", encoding="utf-8")
    labels, values = load_results(jsonl)
    assert labels == ["only"]
    assert values == [0]


# ---------------------------------------------------------------------------
# parse_args
# ---------------------------------------------------------------------------


def test_parse_args_requires_input_and_output() -> None:
    with pytest.raises(SystemExit):
        parse_args([])


def test_parse_args_requires_output() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--input", "results.jsonl"])


def test_parse_args_requires_input() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--output", "chart.png"])


def test_parse_args_returns_paths_when_both_provided() -> None:
    args = parse_args(["--input", "results.jsonl", "--output", "chart.png"])
    assert args.input == pathlib.Path("results.jsonl")
    assert args.output == pathlib.Path("chart.png")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def test_main_creates_output_and_returns_zero(tmp_path: pathlib.Path) -> None:
    jsonl = tmp_path / "results.jsonl"
    jsonl.write_text(
        json.dumps({"id": "tc-1", "passed": True})
        + "\n"
        + json.dumps({"id": "tc-2", "passed": False})
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "subdir" / "chart.png"

    # Use Agg backend to avoid display requirements in headless CI
    import matplotlib

    matplotlib.use("Agg")

    result = main(["--input", str(jsonl), "--output", str(output)])
    assert result == 0
    assert output.exists()


def test_main_creates_parent_directories(tmp_path: pathlib.Path) -> None:
    jsonl = tmp_path / "results.jsonl"
    jsonl.write_text(json.dumps({"id": "x", "passed": True}) + "\n", encoding="utf-8")
    output = tmp_path / "a" / "b" / "c" / "chart.png"

    import matplotlib

    matplotlib.use("Agg")

    result = main(["--input", str(jsonl), "--output", str(output)])
    assert result == 0
    assert output.exists()


def test_main_mock_matplotlib(tmp_path: pathlib.Path) -> None:
    """Verify main() calls savefig and returns 0 without real rendering."""
    jsonl = tmp_path / "results.jsonl"
    jsonl.write_text(json.dumps({"id": "tc-1", "passed": True}) + "\n", encoding="utf-8")
    output = tmp_path / "chart.png"

    mock_fig = MagicMock()
    mock_ax = MagicMock()
    mock_fig.add_subplot.return_value = mock_ax

    with patch("clawops.charts.plt") as mock_plt:
        mock_plt.figure.return_value = mock_fig
        result = main(["--input", str(jsonl), "--output", str(output)])

    assert result == 0
    mock_fig.savefig.assert_called_once_with(output)
