#!/usr/bin/env python3
"""Semantic CLI for the host-side steps of the feature-verification workflow.

These steps run on a fresh runner's bare Python (before ``clawops bootstrap`` creates the
managed environment), so this CLI only imports stdlib-backed helpers; the matrix report lives
in ``feature_report.py``.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from pathlib import Path
from types import FrameType

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
for import_root in (SRC_ROOT, REPO_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from tests.utils.helpers._feature_verification.diagnostics import (  # noqa: E402
    collect_lane_diagnostics,
)
from tests.utils.helpers._feature_verification.fake_model import FakeModelServer  # noqa: E402
from tests.utils.helpers._feature_verification.lane import (  # noqa: E402
    LaneError,
    configure_model_provider,
    enable_systemd_user_manager,
    run_lane,
)
from tests.utils.helpers._feature_verification.processes import (  # noqa: E402
    FakeModelProcessError,
    start_detached,
    stop_detached,
)


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve-fake-model", help="Serve the fake model API (blocks).")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, action="append", required=True)

    start = subparsers.add_parser("start-fake-model", help="Start the fake model API detached.")
    start.add_argument("--host", default="127.0.0.1")
    start.add_argument("--port", type=int, action="append", required=True)
    start.add_argument("--state-dir", type=Path, required=True)

    stop = subparsers.add_parser("stop-fake-model", help="Stop the detached fake model API.")
    stop.add_argument("--state-dir", type=Path, required=True)

    provider = subparsers.add_parser(
        "configure-model-provider",
        help="Point the operator env contract at the fake model server.",
    )
    provider.add_argument("--context", type=Path, required=True)

    subparsers.add_parser(
        "enable-systemd-user",
        help="Start the runner's systemd user manager so user units can be activated.",
    )

    lane = subparsers.add_parser("run-lane", help="Run one lane's feature tests.")
    lane.add_argument("--context", type=Path, required=True)
    lane.add_argument("--lane", required=True)
    lane.add_argument("--results", type=Path, required=True)
    lane.add_argument("--fake-model-dir", type=Path)

    diagnostics = subparsers.add_parser(
        "collect-diagnostics", help="Capture service-manager and gateway diagnostics."
    )
    diagnostics.add_argument("--context", type=Path, required=True)
    diagnostics.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def _serve(host: str, ports: list[int]) -> int:
    """Serve until SIGTERM or SIGINT."""
    server = FakeModelServer(host=host, ports=ports)
    stopped = threading.Event()

    def _stop(signum: int, frame: FrameType | None) -> None:
        del signum, frame
        stopped.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    server.start()
    print(f"fake model server listening on {host}:{server.ports}", flush=True)
    stopped.wait()
    server.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the requested feature-verification subcommand."""
    args = _parse_args(argv)
    try:
        if args.command == "serve-fake-model":
            return _serve(str(args.host), [int(port) for port in args.port])
        if args.command == "start-fake-model":
            state = start_detached(
                script_path=Path(__file__).resolve(),
                host=str(args.host),
                ports=[int(port) for port in args.port],
                state_dir=_resolved(args.state_dir),
            )
            print(json.dumps(state, sort_keys=True))
            return 0
        if args.command == "stop-fake-model":
            print(json.dumps({"stopped": stop_detached(_resolved(args.state_dir))}))
            return 0
        if args.command == "configure-model-provider":
            env_file = configure_model_provider(_resolved(args.context))
            print(f"configured the fake model provider in {env_file}")
            return 0
        if args.command == "enable-systemd-user":
            print(f"systemd user manager ready at {enable_systemd_user_manager()}")
            return 0
        if args.command == "run-lane":
            exit_code = run_lane(
                context_file=_resolved(args.context),
                lane_id=str(args.lane),
                results_path=_resolved(args.results),
                fake_model_dir=(
                    _resolved(args.fake_model_dir) if args.fake_model_dir is not None else None
                ),
            )
            # Feature failures are report data; only a missing results file fails this step.
            print(f"lane pytest exit code: {exit_code}")
            return 0
        if args.command == "collect-diagnostics":
            collect_lane_diagnostics(
                context_file=_resolved(args.context), output_dir=_resolved(args.output_dir)
            )
            return 0
    except (FakeModelProcessError, LaneError) as exc:
        print(f"feature-verification error: {exc}", file=sys.stderr)
        return 1
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
