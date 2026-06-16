"""Railway deployment polling via GraphQL (spec §8.2)."""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

import httpx

logger = logging.getLogger(__name__)

_URL = "https://backboard.railway.app/graphql/v2"
_TERMINAL = {"SUCCESS", "FAILED", "CRASHED", "REMOVED"}

_QUERY = """
query deployments($projectId: String!, $serviceId: String!) {
  deployments(first: 1, input: {projectId: $projectId, serviceId: $serviceId}) {
    edges { node { id status createdAt } }
  }
}
"""


class RailwayClient:
    def __init__(self, config, http_client: Optional[httpx.Client] = None):
        self._config = config
        self._http = http_client

    def configured(self) -> bool:
        return self._config.is_set(self._config.get_raw("RAILWAY_API_TOKEN"))

    def _client(self) -> httpx.Client:
        if self._http is not None:
            return self._http
        token = self._config.require("RAILWAY_API_TOKEN")
        return httpx.Client(timeout=20.0, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"})

    def latest_deployment(self, project_id: str,
                          service_id: str) -> Optional[dict]:
        resp = self._client().post(_URL, json={
            "query": _QUERY,
            "variables": {"projectId": project_id, "serviceId": service_id}})
        resp.raise_for_status()
        edges = (((resp.json().get("data") or {}).get("deployments") or {})
                 .get("edges") or [])
        if not edges:
            return None
        node = edges[0]["node"]
        return {"deployment_id": node.get("id"), "status": node.get("status")}

    def poll(self, project_id: str, service_id: str, *, attempts: int = 30,
             delay: int = 10,
             sleep: Callable[[float], None] = time.sleep) -> dict:
        last = {"status": "UNKNOWN"}
        for i in range(attempts):
            dep = self.latest_deployment(project_id, service_id)
            if dep:
                last = dep
                if dep["status"] in _TERMINAL:
                    return {"target": "railway",
                            "ok": dep["status"] == "SUCCESS", **dep}
            if i < attempts - 1:
                sleep(delay)
        return {"target": "railway", "ok": False, "timed_out": True, **last}
