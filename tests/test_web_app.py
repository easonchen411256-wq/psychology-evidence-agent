import unittest

from fastapi.testclient import TestClient

from psychology_evidence_agent import api_server


class WebAppTests(unittest.TestCase):
    def test_root_serves_research_workspace(self):
        client = TestClient(api_server.app)
        response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Psychology Evidence Agent", response.text)
        self.assertIn("Agent 总览", response.text)
        self.assertIn("/assets/app.js", response.text)
        self.assertEqual(client.get("/assets/app.css").status_code, 200)
        self.assertIn("v1/literature/search", client.get("/assets/app.js").text)
        self.assertIn("v1/agent/runs", client.get("/assets/app.js").text)
        self.assertIn("v1/research-intake/messages", client.get("/assets/app.js").text)
        self.assertIn(
            "v1/agent/runs/${encodeURIComponent(state.runId)}/screening-results",
            client.get("/assets/app.js").text,
        )
        self.assertIn(
            "v1/agent/runs/${encodeURIComponent(state.runId)}/search-results",
            client.get("/assets/app.js").text,
        )
        self.assertIn("search-console", client.get("/assets/app.js").text)
        self.assertIn('role="log"', client.get("/assets/app.js").text)
        self.assertIn("brief-message", client.get("/assets/app.js").text)
        self.assertIn('class="chat-message ${roleClass}"', client.get("/assets/app.js").text)
        self.assertIn("edit-intake-brief", client.get("/assets/app.js").text)
        self.assertIn("pea.intake_state", client.get("/assets/app.js").text)
        self.assertIn("research-context-bar", client.get("/assets/app.js").text)
        self.assertIn("paper-detail-boundary", client.get("/assets/app.js").text)
        self.assertNotIn('id="save-paper"', client.get("/assets/app.js").text)
        self.assertIn("AI 自动检索", client.get("/assets/app.js").text)
        self.assertIn("手动控制", client.get("/assets/app.js").text)
        self.assertIn("model-provider", client.get("/assets/app.js").text)
        self.assertIn("model-api-base", client.get("/assets/app.js").text)
        self.assertIn("model-api-key", client.get("/assets/app.js").text)
        self.assertIn("OpenAI-compatible", client.get("/assets/app.js").text)
        self.assertIn("人工确认并开始检索", client.get("/assets/app.js").text)
        self.assertIn("历史结果", response.text)
        self.assertIn("筛选结果", response.text)
        self.assertIn("/fulltext", client.get("/assets/app.js").text)
        self.assertIn("lawful_access_confirmed", client.get("/assets/app.js").text)


if __name__ == "__main__":
    unittest.main()
