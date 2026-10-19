from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jobos_api.codex_runtime import (
    CODEX_APP_SERVER_RECEIPT_ID,
    CODEX_APP_SERVER_SHA256,
    CODEX_APP_SERVER_VERSION,
)

ROOT = Path(__file__).resolve().parents[3]


def test_current_runtime_pin_and_redistribution_files_match() -> None:
    receipt = json.loads(
        (ROOT / "docs/architecture/codex-runtime-receipt.json").read_text()
    )
    assert receipt["receipt_id"] == CODEX_APP_SERVER_RECEIPT_ID
    assert receipt["candidate"]["version"] == CODEX_APP_SERVER_VERSION
    assert receipt["app_server_binary"]["sha256"] == CODEX_APP_SERVER_SHA256
    package = json.loads((ROOT / "apps/desktop/package.json").read_text())
    resources = package["build"]["extraResources"]
    for name in ("license", "notice"):
        item = receipt["redistribution"][name]
        assert hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest() == item["sha256"]
        assert {
            "from": f'../../{item["path"]}', "to": f"licenses/codex/{name.upper()}"
        } in resources
    for item in receipt["protocol_contracts"]:
        assert hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest() == item["sha256"]


def test_current_protocol_preserves_jobos_lifecycle_and_discovery_fields() -> None:
    schema = json.loads(
        (ROOT / "tests/connected_agents/contracts/codex-0.163.0.v2.schemas.json").read_text()
    )["definitions"]
    required_fields = {
        "ModelListParams": {"cursor", "includeHidden"},
        "ModelListResponse": {"data", "nextCursor"},
        "Model": {"model", "isDefault", "defaultReasoningEffort", "supportedReasoningEfforts"},
        "ThreadStartParams": {"cwd", "approvalPolicy", "approvalsReviewer", "sandbox"},
        "ThreadResumeParams": {"threadId", "cwd", "approvalPolicy", "sandbox"},
        "ThreadReadParams": {"threadId", "includeTurns"},
        "Thread": {"id", "model", "reasoningEffort", "turns"},
        "TurnStartParams": {"threadId", "input", "model", "effort"},
        "TurnInterruptParams": {"threadId", "turnId"},
        "ListMcpServerStatusParams": {"threadId", "detail"},
    }
    for name, fields in required_fields.items():
        assert fields <= schema[name]["properties"].keys()
