from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PanelId = Literal["jobs", "center", "agent"]
LayoutPreset = Literal["research", "review", "agent-focus"]
CenterSurface = Literal["browser", "document"]


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

    @model_validator(mode="after")
    def validate_presets(self):
        if set(self.layouts) != {"research", "review", "agent-focus"}:
            raise ValueError("Workspace snapshot must contain every layout preset")
        return self


class WorkspaceSnapshotCommand(WorkspaceSnapshotBase):
    revision: int = Field(ge=0)
    origin: Literal["user", "mcp", "system"]
    idempotency_key: str = Field(min_length=1, max_length=128)


class WorkspaceSnapshotResponse(WorkspaceSnapshotBase):
    revision: int = Field(ge=0)
    repaired_presets: list[LayoutPreset] = Field(default_factory=list)
