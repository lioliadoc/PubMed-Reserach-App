from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app


class DatabaseTests(unittest.TestCase):
    def test_init_db_and_recent_searches_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "test.sqlite3"
            with mock.patch.object(app, "DB_PATH", db_path):
                app.init_db()
                app.save_search(
                    {
                        "normalized_question": "GLP-1 agonists and obesity",
                        "pubmed_query": "GLP-1 agonists obesity",
                        "status": "success",
                        "message": "summary",
                        "result_count": 12,
                    },
                    "What research does PubMed contain on GLP-1 agonists and obesity?",
                )

                rows = app.recent_searches()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "success")
        self.assertEqual(rows[0]["result_count"], 12)
        self.assertEqual(rows[0]["pubmed_query"], "GLP-1 agonists obesity")


class OpenAIResponseParsingTests(unittest.TestCase):
    def test_extract_openai_text_prefers_output_text(self) -> None:
        text = app.extract_openai_text({"output_text": '{"ok": true}'})
        self.assertEqual(text, '{"ok": true}')

    def test_extract_openai_text_falls_back_to_output_content(self) -> None:
        raw = {
            "output": [
                {
                    "content": [
                        {"type": "output_text", "text": '{"summary": "hello"}'},
                    ]
                }
            ]
        }
        self.assertEqual(app.extract_openai_text(raw), '{"summary": "hello"}')

    def test_extract_openai_text_raises_on_refusal(self) -> None:
        with self.assertRaises(app.AppError):
            app.extract_openai_text({"refusal": "cannot comply"})


class SearchWorkflowTests(unittest.TestCase):
    def test_search_empty_prompt_returns_invalid_input_and_saves_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "test.sqlite3"
            with mock.patch.object(app, "DB_PATH", db_path):
                app.init_db()
                response = app.search("   ")
                rows = app.recent_searches()

        self.assertFalse(response["ok"])
        self.assertEqual(response["status"], "invalid_input")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "invalid_input")

    def test_search_invalid_prompt_from_openai_returns_invalid_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "test.sqlite3"
            with mock.patch.object(app, "DB_PATH", db_path):
                app.init_db()
                with mock.patch.object(
                    app,
                    "openai_json",
                    return_value={
                        "is_relevant": False,
                        "normalized_question": "vacation planning",
                        "pubmed_query": "",
                        "reason": "The input is not relevant to PubMed.",
                    },
                ):
                    response = app.search("best beaches in Europe")
                    rows = app.recent_searches()

        self.assertFalse(response["ok"])
        self.assertEqual(response["status"], "invalid_input")
        self.assertEqual(response["normalized_question"], "vacation planning")
        self.assertEqual(rows[0]["status"], "invalid_input")

    def test_search_success_calls_mcp_and_saves_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "test.sqlite3"
            with mock.patch.object(app, "DB_PATH", db_path):
                app.init_db()
                with mock.patch.object(
                    app,
                    "openai_json",
                    return_value={
                        "is_relevant": True,
                        "normalized_question": "GLP-1 receptor agonists for obesity",
                        "pubmed_query": "GLP-1 receptor agonists obesity",
                        "reason": "",
                    },
                ) as mock_openai, mock.patch.object(
                    app,
                    "call_mcp_tool",
                    return_value={
                        "query": "GLP-1 receptor agonists obesity",
                        "total_count": 5,
                        "articles": [{"title": "Paper 1", "journal": "Journal", "pubdate": "2024"}],
                    },
                ) as mock_mcp, mock.patch.object(
                    app, "summarize_results", return_value="Short summary"
                ):
                    response = app.search(
                        "What research does PubMed contain on GLP-1 receptor agonists for obesity?"
                    )
                    rows = app.recent_searches()

        self.assertTrue(response["ok"])
        self.assertEqual(response["status"], "success")
        self.assertEqual(response["result_count"], 5)
        self.assertEqual(response["message"], "Short summary")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "success")
        mock_openai.assert_called_once()
        mock_mcp.assert_called_once_with(
            "search_pubmed", {"query": "GLP-1 receptor agonists obesity", "max_results": 8}
        )


class McpClientTests(unittest.TestCase):
    def test_call_mcp_tool_parses_jsonrpc_result(self) -> None:
        request_id = "request-123"

        class FakeProcess:
            def communicate(self, input: str, timeout: int) -> tuple[str, str]:
                lines = input.splitlines()
                request_payload = json.loads(lines[2])
                self.assertEqual(request_payload["method"], "tools/call")
                response = {
                    "jsonrpc": "2.0",
                    "id": request_payload["id"],
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps({"query": "topic", "total_count": 3, "articles": []}),
                            }
                        ]
                    },
                }
                return json.dumps(response) + "\n", ""

            def assertEqual(self, left: object, right: object) -> None:
                if left != right:
                    raise AssertionError(f"{left!r} != {right!r}")

        with mock.patch.object(app.uuid, "uuid4", side_effect=["init-1", request_id]), mock.patch.object(
            app.subprocess, "Popen", return_value=FakeProcess()
        ):
            result = app.call_mcp_tool("search_pubmed", {"query": "topic", "max_results": 8})

        self.assertEqual(result["total_count"], 3)
        self.assertEqual(result["query"], "topic")


if __name__ == "__main__":
    unittest.main()
