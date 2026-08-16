from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ApiErrorResponse(ApiResponse):
    error_schema: Literal["jobos-error-v1"]
    code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    message: str = Field(min_length=1, max_length=500)
    retryable: bool
    correlation_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    # Retained for v1 clients while they migrate from FastAPI's legacy shape.
    detail: Any | None = None


class HealthResponse(ApiResponse):
    status: Literal["ready"]
    service: Literal["jobos-api"]
    version: str = Field(min_length=1)
    state_schema: int = Field(ge=1)
    transport: Literal["local-loopback", "private-remote"]
    agent: Literal["not-configured", "online", "connecting", "offline"]
    artifact_storage: Literal["available", "unavailable"]
    artifact_gateway: Literal["not-configured", "available", "unavailable"]


class VersionResponse(ApiResponse):
    api_version: str = Field(min_length=1)
    contract: Literal["jobos-api-v1"]
    error_schema: Literal["jobos-error-v1"]


class DeviceSessionResponse(ApiResponse):
    authenticated: Literal[True]
    transport: Literal["local-loopback", "private-remote"]
    desktop: Literal["connected", "disconnected"]
    api_version: str = Field(min_length=1)
