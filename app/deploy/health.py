"""Health-check polling (spec §8.2) — html status or json key/value."""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

import httpx

logger = logging.getLogger(__name__)


class HealthChecker:
    def __init__(self, config, http_client: Optional[httpx.Client] = None):
        self._config = config
        self._http = http_client

    def _client(self) -> httpx.Client:
        return self._http or httpx.Client(timeout=10.0, follow_redirects=True)

    def check(self, project: str, *, attempts: int = 5, delay: int = 10,
              sleep: Callable[[float], None] = time.sleep) -> dict:
        up = project.upper()
        url = self._config.get_raw(f"PROJECT_{up}_HEALTH_CHECK_URL")
        if not url:
            return {"target": "health_check", "ok": False,
                    "detail": "no health check URL configured"}
        fmt = self._config.get_raw(f"PROJECT_{up}_HEALTH_CHECK_FORMAT", "html")
        expected_status = int(
            self._config.get(f"PROJECT_{up}_HEALTH_CHECK_EXPECTED_STATUS", 200))
        client = self._client()

        last = {}
        for i in range(attempts):
            try:
                resp = client.get(url)
                ok = resp.status_code == expected_status
                detail = {"status": resp.status_code}
                if ok and fmt == "json":
                    key = self._config.get_raw(f"PROJECT_{up}_HEALTH_CHECK_JSON_KEY")
                    expected = self._config.get_raw(
                        f"PROJECT_{up}_HEALTH_CHECK_EXPECTED_VALUE")
                    value = resp.json().get(key)
                    ok = (value == expected)
                    detail["value"] = value
                last = {"target": "health_check", "ok": ok, "url": url, **detail}
                if ok:
                    return last
            except (httpx.HTTPError, ValueError) as exc:
                last = {"target": "health_check", "ok": False, "url": url,
                        "error": str(exc)}
            if i < attempts - 1:
                sleep(delay)
        return last
