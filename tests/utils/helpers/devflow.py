"""Reusable helpers for devflow tests."""

from __future__ import annotations

import pathlib
import shutil
import subprocess

from tests.utils.helpers.cli import write_status_script
from tests.utils.helpers.repo import REPO_ROOT

FIXTURE_REPOS_ROOT = REPO_ROOT / "tests" / "fixtures" / "repos"


def write_strongclaw_shaped_repo(repo_root: pathlib.Path) -> None:
    """Create a minimal Strongclaw-shaped repository."""
    (repo_root / "src" / "clawops").mkdir(parents=True, exist_ok=True)
    (repo_root / "Makefile").write_text("install:\n\tuv sync --locked\n", encoding="utf-8")
    (repo_root / "pyproject.toml").write_text(
        '[project]\nname = "clawops"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    (repo_root / "README.md").write_text("# Fixture repo\n", encoding="utf-8")
    (repo_root / "src" / "clawops" / "__init__.py").write_text("", encoding="utf-8")


def install_fake_devflow_backends(
    bin_dir: pathlib.Path,
    *,
    create_expected_artifacts: bool = True,
) -> None:
    """Install fake ACPX, Codex, and Claude binaries into *bin_dir*."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    _write_fake_devflow_acpx(bin_dir, create_expected_artifacts=create_expected_artifacts)
    write_status_script(bin_dir, "codex", stdout_text="Logged in using ChatGPT")
    write_status_script(bin_dir, "claude", stdout_text='{"status":"authenticated"}')


def _write_fake_devflow_acpx(
    bin_dir: pathlib.Path,
    *,
    create_expected_artifacts: bool,
) -> None:
    """Write a fake ACPX executable shaped for devflow integration tests."""
    target = bin_dir / "acpx"
    target.write_text(
        (
            "#!/usr/bin/env python3\n"
            "from __future__ import annotations\n"
            "\n"
            "import pathlib\n"
            "import re\n"
            "import sys\n"
            "\n"
            "CREATE_EXPECTED_ARTIFACTS = "
            f"{'True' if create_expected_artifacts else 'False'}\n"
            "\n"
            "prompt = sys.argv[-1] if len(sys.argv) > 1 else ''\n"
            "stdout = 'fake-acpx ' + ' '.join(sys.argv[1:])\n"
            "print(stdout)\n"
            "print('stderr from fake-acpx', file=sys.stderr)\n"
            "if not CREATE_EXPECTED_ARTIFACTS:\n"
            "    raise SystemExit(0)\n"
            "repo_root_match = re.search(r'^- repo_root: (?P<value>.+)$', prompt, re.MULTILINE)\n"
            "run_id_match = re.search(r'^- run_id: (?P<value>.+)$', prompt, re.MULTILINE)\n"
            "stage_match = re.search(r'^- stage: (?P<value>.+)$', prompt, re.MULTILINE)\n"
            "artifact_section = re.search(\n"
            "    r'^Expected artifacts:\\n(?P<body>(?:- .*(?:\\n|$))*)',\n"
            "    prompt,\n"
            "    re.MULTILINE,\n"
            ")\n"
            "if repo_root_match is None or run_id_match is None or artifact_section is None:\n"
            "    raise SystemExit(0)\n"
            "repo_root = pathlib.Path(repo_root_match.group('value').strip())\n"
            "run_id = run_id_match.group('value').strip()\n"
            "stage = 'artifact' if stage_match is None else stage_match.group('value').strip()\n"
            "run_root = repo_root / '.clawops' / 'devflow' / run_id\n"
            "for raw_line in artifact_section.group('body').splitlines():\n"
            "    line = raw_line.strip()\n"
            "    if not line.startswith('- '):\n"
            "        continue\n"
            "    rel_path = line[2:].strip()\n"
            "    if not rel_path or rel_path == 'none':\n"
            "        continue\n"
            "    target_path = run_root / rel_path\n"
            "    target_path.parent.mkdir(parents=True, exist_ok=True)\n"
            "    target_path.write_text(f'{stage} artifact\\n', encoding='utf-8')\n"
        ),
        encoding="utf-8",
    )
    target.chmod(0o755)


def init_git_repo(repo_root: pathlib.Path) -> None:
    """Initialize a git repository with one initial commit."""
    subprocess.run(
        ["git", "init", "-b", "main", str(repo_root)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_root), "config", "user.email", "tests@example.com"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_root), "config", "user.name", "Test User"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_root), "add", "."],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_root), "commit", "-m", "initial"],
        check=True,
        capture_output=True,
        text=True,
    )


def copy_fixture_repo(name: str, destination: pathlib.Path) -> pathlib.Path:
    """Copy one fixture repository into *destination* and return it."""
    source = FIXTURE_REPOS_ROOT / name
    shutil.copytree(source, destination)
    return destination
