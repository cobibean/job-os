from __future__ import annotations

from typing import Any

import httpx


class JobOsMcpClient:
    """Thin agent Adapter over the authenticated JobOS application Interface."""

    def __init__(
        self,
        *,
        base_url: str,
        device_token: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {device_token}"},
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def list_jobs(
        self,
        *,
        sort: str = "manual",
        query: str | None = None,
        status_group: str | None = None,
    ) -> dict[str, Any]:
        params = {"sort": sort}
        if query:
            params["query"] = query
        if status_group:
            params["status_group"] = status_group
        return await self._request("GET", "/v1/jobs", params=params)

    async def inspect_job(self, job_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/v1/jobs/{job_id}")

    async def select_job(self, job_id: str) -> dict[str, Any]:
        return await self._request(
            "PUT",
            "/v1/workspace/jobs/selection",
            json={"job_id": job_id, "origin": "mcp"},
        )

    async def reorder_jobs(self, job_ids: list[str]) -> dict[str, Any]:
        return await self._request(
            "PUT",
            "/v1/jobs/order",
            json={"job_ids": job_ids, "origin": "mcp"},
        )

    async def update_status(
        self,
        job_id: str,
        target_status: str,
        *,
        reason: str | None = None,
    ) -> dict[str, Any]:
        payload = {"target_status": target_status, "origin": "mcp"}
        if reason is not None:
            payload["reason"] = reason
        return await self._request("PUT", f"/v1/jobs/{job_id}/status", json=payload)

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = await self._client.request(method, path, **kwargs)
        response.raise_for_status()
        return dict(response.json())
