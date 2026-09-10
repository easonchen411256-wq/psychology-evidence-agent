import json
import tempfile
import unittest
from pathlib import Path

import httpx

from psychology_evidence_agent.adapters.llm.openai_compatible import (
    ModelConnection,
    OpenAICompatibleAdapter,
)
from psychology_evidence_agent.domain.errors import StructuredOutputError


class OpenAICompatibleAdapterTests(unittest.TestCase):
    def _schema(self, root: str) -> Path:
        path = Path(root) / "result.schema.json"
        path.write_text(
            json.dumps(
                {
                    "type": "object",
                    "required": ["answer"],
                    "additionalProperties": False,
                    "properties": {"answer": {"type": "string"}},
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_posts_compatible_chat_request_and_validates_schema(self):
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["authorization"] = request.headers.get("Authorization")
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": '```json\n{"answer":"ok"}\n```'}}]},
            )

        connection = ModelConnection(
            provider="custom",
            model="research-model",
            api_base="https://models.example/v1",
            api_key="top-secret",
        )
        client = httpx.Client(transport=httpx.MockTransport(handler))
        adapter = OpenAICompatibleAdapter(connection, client=client)
        with tempfile.TemporaryDirectory() as root:
            result = adapter.generate(
                task_instruction="Return a test result.",
                stdin_payload="input",
                schema_path=self._schema(root),
            )

        self.assertEqual(result, {"answer": "ok"})
        self.assertEqual(captured["url"], "https://models.example/v1/chat/completions")
        self.assertEqual(captured["authorization"], "Bearer top-secret")
        self.assertEqual(captured["body"]["model"], "research-model")
        self.assertEqual(captured["body"]["response_format"], {"type": "json_object"})
        client.close()

    def test_rejects_output_outside_project_schema_without_leaking_key(self):
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200, json={"choices": [{"message": {"content": '{"wrong":true}'}}]}
                )
            )
        )
        adapter = OpenAICompatibleAdapter(
            ModelConnection(
                provider="custom",
                model="research-model",
                api_base="https://models.example/v1",
                api_key="never-show-this",
            ),
            client=client,
        )
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(StructuredOutputError) as raised:
                adapter.generate(
                    task_instruction="Return a test result.",
                    stdin_payload="input",
                    schema_path=self._schema(root),
                )

        self.assertNotIn("never-show-this", str(raised.exception))
        client.close()


if __name__ == "__main__":
    unittest.main()
