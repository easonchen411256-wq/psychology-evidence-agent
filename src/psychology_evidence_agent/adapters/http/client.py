from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from ...domain.errors import (
    ExternalRateLimitError,
    ExternalResponseError,
    ExternalTimeoutError,
    ExternalUnavailableError,
)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    backoff_seconds: float = 0.1

    def __post_init__(self) -> None:
        if self.max_attempts < 1 or self.backoff_seconds < 0:
            raise ValueError("max_attempts must be positive and backoff_seconds non-negative")


class HttpJsonClient:
    def __init__(
        self,
        client: httpx.Client,
        *,
        retry_policy: RetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._policy = retry_policy or RetryPolicy()
        self._sleep = sleep

    def get_json(self, url: str, **kwargs: Any) -> dict[str, Any]:
        for attempt in range(1, self._policy.max_attempts + 1):
            try:
                response = self._client.get(url, **kwargs)
            except httpx.TimeoutException as error:
                if attempt == self._policy.max_attempts:
                    raise ExternalTimeoutError("External request timed out.") from error
                self._wait(attempt)
                continue
            except httpx.RequestError as error:
                if attempt == self._policy.max_attempts:
                    raise ExternalUnavailableError("External request failed.") from error
                self._wait(attempt)
                continue
            if response.status_code == 429:
                if attempt == self._policy.max_attempts:
                    raise ExternalRateLimitError("External service rate limit reached.")
                self._wait(attempt, response.headers.get("Retry-After"))
                continue
            if response.status_code in {500, 502, 503, 504}:
                if attempt == self._policy.max_attempts:
                    raise ExternalUnavailableError("External service is temporarily unavailable.")
                self._wait(attempt)
                continue
            if response.is_error:
                raise ExternalResponseError(
                    f"External service returned HTTP {response.status_code}."
                )
            try:
                payload = response.json()
            except ValueError as error:
                raise ExternalResponseError("External service returned invalid JSON.") from error
            if not isinstance(payload, dict):
                raise ExternalResponseError("External service returned an unexpected JSON value.")
            return payload
        raise ExternalUnavailableError("External request did not complete.")

    def _wait(self, attempt: int, retry_after: str | None = None) -> None:
        try:
            delay = float(retry_after) if retry_after else self._policy.backoff_seconds * attempt
        except ValueError:
            delay = self._policy.backoff_seconds * attempt
        self._sleep(max(0.0, delay))
