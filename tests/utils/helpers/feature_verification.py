"""Public feature-verification helper facade."""

from tests.utils.helpers._feature_verification.acp_stub_agent import (
    REPLY_PREFIX as ACP_STUB_REPLY_PREFIX,
)
from tests.utils.helpers._feature_verification.diagnostics import collect_lane_diagnostics
from tests.utils.helpers._feature_verification.fake_model import (
    CHAT_MODEL_ID,
    REPLY_PREFIX,
    FakeModelServer,
    canned_reply,
    deterministic_embedding,
)
from tests.utils.helpers._feature_verification.host import (
    CommandOutcome,
    FeatureHost,
    extract_json,
)
from tests.utils.helpers._feature_verification.http import (
    HttpResponse,
    basic_auth,
    http_request,
)
from tests.utils.helpers._feature_verification.lane import (
    FAKE_CHAT_MODEL_REF,
    LaneError,
    configure_model_provider,
    enable_systemd_user_manager,
    run_lane,
)
from tests.utils.helpers._feature_verification.manifest import (
    Feature,
    FeatureManifest,
    FeatureVerificationError,
    Lane,
    load_manifest,
)
from tests.utils.helpers._feature_verification.processes import (
    FakeModelProcessError,
    start_detached,
    stop_detached,
)
from tests.utils.helpers._feature_verification.recorder import LaneRecorder, is_feature_suite_item
from tests.utils.helpers._feature_verification.reporting import (
    build_report,
    render_markdown,
    write_lane_report,
)
from tests.utils.helpers._feature_verification.results import (
    UNVERIFIABLE_SKIP_PREFIX,
    FeatureResult,
    LaneResults,
    load_lane_results,
)
from tests.utils.helpers._feature_verification.runtime import (
    GatewayController,
    gateway_rpc_ok,
    gateway_status,
    plugin_statuses,
    service_active,
    tcp_open,
)

__all__ = [
    "ACP_STUB_REPLY_PREFIX",
    "CHAT_MODEL_ID",
    "FAKE_CHAT_MODEL_REF",
    "REPLY_PREFIX",
    "UNVERIFIABLE_SKIP_PREFIX",
    "CommandOutcome",
    "FakeModelProcessError",
    "FakeModelServer",
    "Feature",
    "FeatureHost",
    "FeatureManifest",
    "FeatureResult",
    "FeatureVerificationError",
    "GatewayController",
    "HttpResponse",
    "Lane",
    "LaneError",
    "LaneRecorder",
    "LaneResults",
    "basic_auth",
    "build_report",
    "canned_reply",
    "collect_lane_diagnostics",
    "configure_model_provider",
    "deterministic_embedding",
    "enable_systemd_user_manager",
    "extract_json",
    "gateway_rpc_ok",
    "gateway_status",
    "http_request",
    "is_feature_suite_item",
    "load_lane_results",
    "load_manifest",
    "plugin_statuses",
    "render_markdown",
    "run_lane",
    "service_active",
    "start_detached",
    "stop_detached",
    "tcp_open",
    "write_lane_report",
]
