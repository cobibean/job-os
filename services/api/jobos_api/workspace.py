from typing import Literal
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PanelId = Literal["jobs", "center", "agent"]
LayoutPreset = Literal["research", "review", "agent-focus"]
CenterSurface = Literal["browser", "document"]


class BrowserTabMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tab_id: str = Field(min_length=1, max_length=128)
    url: str = Field(min_length=1, max_length=8192)
    title: str = Field(default="New tab", max_length=512)
    favicon_url: str | None = Field(default=None, max_length=8192)
    associated_job_id: str | None = Field(default=None, max_length=512)

    @field_validator("url")
    @classmethod
    def validate_browser_url(cls, value: str) -> str:
        if value != "about:blank" and not value.startswith(("http://", "https://")):
            raise ValueError("Browser tabs may restore only ordinary web URLs")
        if value != "about:blank":
            parsed = urlsplit(value)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise ValueError("Browser tab URL is not an ordinary website")
            if parsed.username or parsed.password or parsed.fragment:
                raise ValueError("Credential-bearing browser URLs cannot be persisted")
            sensitive = {
                "access_token", "auth_token", "authorization", "bearer_token",
                "code", "credential", "id_token", "jwt", "password",
                "refresh_token", "samlresponse", "secret", "session", "session_id",
            }
            if any(key.lower() in sensitive for key, _ in parse_qsl(parsed.query)):
                raise ValueError("Credential-bearing browser URLs cannot be persisted")
        return value

    @field_validator("favicon_url")
    @classmethod
    def validate_favicon_url(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(("http://", "https://")):
            raise ValueError("Favicon URL is not supported")
        return cls.validate_browser_url(value) if value is not None else None


class PanelLayout(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    order: list[PanelId]
    widths: dict[PanelId, int]
    collapsed: list[PanelId]

    @model_validator(mode="after")
    def validate_complete_layout(self):
        panel_ids = {"jobs", "center", "agent"}
        if len(self.order) != 3 or set(self.order) != panel_ids:
            raise ValueError("Panel order must contain every primary panel exactly once")
        if set(self.widths) != panel_ids or any(
            width < 180 or width > 1600 for width in self.widths.values()
        ):
            raise ValueError("Panel widths must be complete and usable")
        if len(self.collapsed) != len(set(self.collapsed)):
            raise ValueError("Collapsed panels must be unique")
        return self


class WorkspaceSnapshotBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    selected_preset: LayoutPreset
    layouts: dict[LayoutPreset, PanelLayout]
    selected_job_id: str | None
    active_center_surface: CenterSurface
    browser_tabs: list[BrowserTabMetadata] = Field(default_factory=list, max_length=50)
    active_browser_tab_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_presets(self):
        if set(self.layouts) != {"research", "review", "agent-focus"}:
            raise ValueError("Workspace snapshot must contain every layout preset")
        return self

    @model_validator(mode="after")
    def validate_browser_tabs(self):
        tab_ids = [tab.tab_id for tab in self.browser_tabs]
        if len(tab_ids) != len(set(tab_ids)):
            raise ValueError("Browser tab IDs must be unique")
        if self.active_browser_tab_id is not None and self.active_browser_tab_id not in tab_ids:
            raise ValueError("Active browser tab must refer to a restored tab")
        return self


class WorkspaceSnapshotCommand(WorkspaceSnapshotBase):
    revision: int = Field(ge=0)
    origin: Literal["user", "mcp", "system"]
    idempotency_key: str = Field(min_length=1, max_length=128)


class WorkspaceSnapshotResponse(WorkspaceSnapshotBase):
    revision: int = Field(ge=0)
    repaired_presets: list[LayoutPreset] = Field(default_factory=list)
    repaired_browser: bool = False
