from __future__ import annotations

import io
import json
import unittest
from unittest import mock

import pubmed_mcp_server as mcp


class PubMedServerTests(unittest.TestCase):
    def test_search_pubmed_requires_query(self) -> None:
        with self.assertRaises(ValueError):
            mcp.search_pubmed({"query": ""})

    def test_search_pubmed_combines_esearch_and_esummary(self) -> None:
        def fake_ncbi_get(endpoint: str, params: dict[str, object]) -> dict[str, object]:
            if endpoint == "esearch.fcgi":
                self.assertEqual(params["term"], "GLP-1 obesity")
                return {"esearchresult": {"idlist": ["123"], "count": "42"}}
            if endpoint == "esummary.fcgi":
                self.assertEqual(params["id"], "123")
                return {
                    "result": {
                        "123": {
                            "title": "Sample paper",
                            "fulljournalname": "Medical Journal",
                            "pubdate": "2024",
                            "authors": [{"name": "Author One"}, {"name": "Author Two"}],
                        }
                    }
                }
            raise AssertionError(f"Unexpected endpoint: {endpoint}")

        with mock.patch.object(mcp, "ncbi_get", side_effect=fake_ncbi_get):
            result = mcp.search_pubmed({"query": "GLP-1 obesity", "max_results": 3})

        self.assertEqual(result["total_count"], 42)
        self.assertEqual(len(result["articles"]), 1)
        self.assertEqual(result["articles"][0]["pmid"], "123")
        self.assertEqual(result["articles"][0]["authors"], ["Author One", "Author Two"])

    def test_list_tools_exposes_search_pubmed(self) -> None:
        tools = mcp.list_tools()["tools"]
        self.assertEqual(tools[0]["name"], "search_pubmed")

    def test_main_handles_tools_list(self) -> None:
        request = "\n".join(
            [
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": "1",
                        "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {}},
                    }
                ),
                json.dumps({"jsonrpc": "2.0", "id": "2", "method": "tools/list", "params": {}}),
                "",
            ]
        )

        with mock.patch("sys.stdin", io.StringIO(request)), mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            mcp.main()

        output_lines = [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]
        self.assertEqual(output_lines[0]["result"]["serverInfo"]["name"], "pubmed-mcp-server")
        self.assertEqual(output_lines[1]["result"]["tools"][0]["name"], "search_pubmed")


if __name__ == "__main__":
    unittest.main()
