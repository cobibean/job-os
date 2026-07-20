from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .browser_policy import (
    BROWSER_TAB_LIMIT,
    BROWSER_TITLE_LIMIT,
    BROWSER_URL_LIMIT,
    browser_title_contains_credentials,
    safe_browser_url,
)

PanelId = Literal["jobs", "center", "agent"]
LayoutPreset = Literal["research", "review", "agent-focus"]
CenterSurface = Literal["browser", "document"]


class BrowserTabMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tab_id: str = Field(min_length=1, max_length=128)
    url: str = Field(min_length=1, max_length=BROWSER_URL_LIMIT)
    title: str = Field(default="New tab", max_length=BROWSER_TITLE_LIMIT)
    favicon_url: str | None = Field(default=None, max_length=BROWSER_URL_LIMIT)
    associated_job_id: str | None = Field(default=None, max_length=512)

    @field_validator("title")
    @classmethod
    def validate_browser_title(cls, value: str) -> str:
        if browser_title_contains_credentials(value):
            raise ValueError("Credential-bearing browser titles cannot be persisted")
        return value

    @field_validator("url")
    @classmethod
    def validate_browser_url(cls, value: str) -> str:
        if not safe_browser_url(value, allow_blank=True):
            raise ValueError("Credential-bearing or unsupported browser URLs cannot be persisted")
        return value

    @field_validator("favicon_url")
    @classmethod
    def validate_favicon_url(cls, value: str | None) -> str | None:
        if value is not None and not safe_browser_url(value, allow_blank=False):
            raise ValueError("Favicon URL is not supported")
        return value


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
    browser_tabs: list[BrowserTabMetadata] = Field(
        default_factory=list, max_length=BROWSER_TAB_LIMIT
    )
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
