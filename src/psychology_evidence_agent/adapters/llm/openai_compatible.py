"""OpenAI-compatible structured-output adapter for user-selected model APIs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from jsonschema import ValidationError, validate

from ...domain.errors import StructuredOutputError

_JSON_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)


@dataclass(frozen=True)
class ModelConnection:
    """Ephemeral connection details; callers must never persist ``api_key``."""

    provider: str
    model: str
    api_base: str
    api_key: str
    timeout_seconds: float = 120.0
    json_mode: bool = True

    def safe_descriptor(self) -> dict[str, str]:
        """Return the non-secret fields that may be written to run artifacts."""
        return {"provider": self.provider, "model": self.model}


class OpenAICompatibleAdapter:
    """Call a Chat Completions compatible endpoint and enforce the project schema."""

    def __init__(
        self,
        connection: ModelConnection,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self._connection = connection
        self._client = client

    def generate(
        self, *, task_instruction: str, stdin_payload: str, schema_path: Path
    ) -> dict[str, Any]:
        try:
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise StructuredOutputError("Structured-output schema could not be loaded.") from error

        request_payload: dict[str, Any] = {
            "model": self._connection.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"{task_instruction}\n\nReturn only one JSON object that conforms to "
                        "the JSON Schema supplied by the user. Do not wrap it in Markdown."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"INPUT:\n{stdin_payload}\n\nJSON SCHEMA:\n"
                        f"{json.dumps(schema, ensure_ascii=False)}"
                    ),
                },
            ],
            "stream": False,
        }
        if self._connection.json_mode:
            request_payload["response_format"] = {"type": "json_object"}

        own_client = self._client is None
        client = self._client or httpx.Client(timeout=self._connection.timeout_seconds)
        try:
            response = client.post(
                f"{self._connection.api_base.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._connection.api_key}",
                    "Content-Type": "application/json",
                },
                json=request_payload,
            )
            if response.is_error:
                raise StructuredOutputError(f"Model provider returned HTTP {response.status_code}.")
            payload = response.json()
        except httpx.TimeoutException as error:
            raise StructuredOutputError("Model provider request timed out.") from error
        except httpx.RequestError as error:
            raise StructuredOutputError("Model provider could not be reached.") from error
        except ValueError as error:
            raise StructuredOutputError("Model provider returned invalid JSON.") from error
        finally:
            if own_client:
                client.close()

        content = self._message_content(payload)
        fenced = _JSON_FENCE.fullmatch(content.strip())
        if fenced:
            content = fenced.group(1)
        try:
            result = json.loads(content)
        except ValueError as error:
            raise StructuredOutputError("Model provider did not return a JSON object.") from error
        if not isinstance(result, dict):
            raise StructuredOutputError("Model provider did not return a JSON object.")
        try:
            validate(instance=result, schema=schema)
        except ValidationError as error:
            raise StructuredOutputError(
                "Model provider output did not match the required schema."
            ) from error
        return result

    @staticmethod
    def _message_content(payload: Any) -> str:
        if not isinstance(payload, dict):
            raise StructuredOutputError("Model provider returned an unexpected response.")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise StructuredOutputError("Model provider returned an unexpected response.")
        message = choices[0].get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise StructuredOutputError("Model provider returned an unexpected response.")
        return message["content"]
