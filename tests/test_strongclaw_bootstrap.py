from __future__ import annotations

from types import SimpleNamespace

from clawops import strongclaw_bootstrap


def test_resolve_node_command_falls_back_to_nodejs(monkeypatch) -> None:
    """Prefer `nodejs` when `node` is unavailable."""

    monkeypatch.setattr(
        strongclaw_bootstrap,
        "command_exists",
        lambda command_name: command_name == "nodejs",
    )

    assert strongclaw_bootstrap._resolve_node_command() == "nodejs"


def test_node_satisfies_minimum_uses_resolved_command(monkeypatch) -> None:
    """Version checks should use the resolved Node.js executable name."""

    seen_commands: list[list[str]] = []

    def fake_run_command(command: list[str], **_: object) -> SimpleNamespace:
        seen_commands.append(command)
        return SimpleNamespace(ok=True)

    monkeypatch.setattr(strongclaw_bootstrap, "_resolve_node_command", lambda: "nodejs")
    monkeypatch.setattr(strongclaw_bootstrap, "run_command", fake_run_command)

    assert strongclaw_bootstrap._node_satisfies_minimum() is True
    assert seen_commands == [
        [
            "nodejs",
            "-e",
            "const [major, minor] = process.versions.node.split('.').map(Number); process.exit(major > 22 || (major === 22 && minor >= 16) ? 0 : 1);",
        ]
    ]


def test_install_qmd_asset_writes_wrapper_with_resolved_node_command(monkeypatch, tmp_path) -> None:
    """The generated QMD wrapper should invoke the resolved Node.js command."""

    qmd_install_prefix = tmp_path / ".strongclaw" / "qmd"
    qmd_dist_entry = (
        qmd_install_prefix / "node_modules" / "@tobilu" / "qmd" / "dist" / "cli" / "qmd.js"
    )
    qmd_dist_entry.parent.mkdir(parents=True, exist_ok=True)
    qmd_dist_entry.write_text("console.log('ok');\n", encoding="utf-8")

    monkeypatch.setattr(strongclaw_bootstrap, "_resolve_node_command", lambda: "nodejs")
    monkeypatch.setattr(
        strongclaw_bootstrap,
        "command_exists",
        lambda command_name: command_name == "npm",
    )
    monkeypatch.setattr(strongclaw_bootstrap, "_stream_checked", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        strongclaw_bootstrap,
        "strongclaw_qmd_install_dir",
        lambda **_: qmd_install_prefix,
    )
    monkeypatch.setattr(
        strongclaw_bootstrap,
        "run_command",
        lambda *args, **kwargs: SimpleNamespace(ok=True),
    )

    wrapper_path = strongclaw_bootstrap.install_qmd_asset(home_dir=tmp_path)

    assert wrapper_path.read_text(encoding="utf-8").endswith(
        f'exec nodejs "{qmd_dist_entry}" "$@"\n'
    )
