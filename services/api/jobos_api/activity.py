import json
from dataclasses import replace

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .agent_gateway import GatewayEvent
from .redaction import redact_detail

_LABELS = {
    "shell": "Running command",
    "read_file": "Reading file",
    "write_file": "Updating file",
    "browser.open": "Using browser",
}


class ActivityReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(min_length=1, max_length=160)
    state: str = Field(pattern=r"^(working|completed|failed|waiting)$")
    detail: dict[str, object] = Field(default_factory=dict)
    origin: str = Field(pattern=r"^mcp$")
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("detail")
    @classmethod
    def bounded_detail(cls, value: dict[str, object]) -> dict[str, object]:
        if len(value) > 20 or len(json.dumps(value, default=str)) > 10_000:
            raise ValueError("Activity detail exceeds JobOS bounds")
        return value


class ActivityReportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: int


class ActivityNormalizer:
    def __init__(self) -> None:
        self._activities: dict[str, GatewayEvent] = {}

    def normalize(self, frame: dict[str, object]) -> GatewayEvent:
        frame_type = str(frame.get("type", ""))
        tool_id = str(frame.get("tool_id", ""))
        supported_types = {
            "tool.start",
            "tool.progress",
            "tool.complete",
            "tool.output_risk",
        }
        if not tool_id or frame_type not in supported_types:
            raise ValueError("Malformed or unsupported tool frame")
        previous = self._activities.get(tool_id)
        tool_name = str(frame.get("name") or frame.get("tool_name") or "")
        summary = (
            _LABELS.get(
                tool_name,
                f"Using {tool_name.replace('_', ' ')}" if tool_name else "Agent action",
            )
            if previous is None
            else previous.summary
        )
        status = str(frame.get("status", ""))
        state = "working"
        if frame_type == "tool.complete":
            state = (
                "completed" if status in {"", "complete", "completed", "succeeded"} else "failed"
            )
        elif frame_type == "tool.output_risk":
            state = "waiting"
        detail = redact_detail(frame)
        event = GatewayEvent(
            event_type="activity",
            state=state,
            summary=summary,
            detail=detail,
            activity_id=tool_id,
            source_event_id=str(frame.get("event_id")) if frame.get("event_id") else None,
        )
        self._activities[tool_id] = event
        return replace(event)
