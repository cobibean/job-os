from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HealthResponse(ApiResponse):
    status: Literal["ready"]
    service: Literal["jobos-api"]
    version: str = Field(min_length=1)
    state_schema: int = Field(ge=1)
    agent_connection: Literal["online", "connecting", "offline"]


class VersionResponse(ApiResponse):
    api_version: str = Field(min_length=1)
    contract: Literal["jobos-v1-phase7-parity"]


class DeviceSessionResponse(ApiResponse):
    authenticated: Literal[True]
    transport: Literal["private-tailscale"]
    api_version: str = Field(min_length=1)
