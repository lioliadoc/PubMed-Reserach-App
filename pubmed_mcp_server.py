#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from typing import Any
from urllib import error, parse, request


NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def ncbi_get(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    base_params = {
        "tool": os.environ.get("NCBI_TOOL", "pubmed-research-mvp"),
        "email": os.environ.get("NCBI_EMAIL", "demo@example.com"),
    }
    api_key = os.environ.get("NCBI_API_KEY")
    if api_key:
        base_params["api_key"] = api_key
    query = parse.urlencode({**base_params, **params})
    url = f"{NCBI_BASE}/{endpoint}?{query}"
    req = request.Request(url, headers={"Accept": "application/json"})
    with request.urlopen(req, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def search_pubmed(arguments: dict[str, Any]) -> dict[str, Any]:
    query = str(arguments.get("query", "")).strip()
    max_results = max(1, min(int(arguments.get("max_results", 8)), 20))
    if not query:
        raise ValueError("query is required")

    search = ncbi_get(
        "esearch.fcgi",
        {
            "db": "pubmed",
            "term": query,
            "retmode": "json",
            "retmax": max_results,
            "sort": "relevance",
        },
    )
    id_list = search.get("esearchresult", {}).get("idlist", [])
    count = int(search.get("esearchresult", {}).get("count", "0"))
    articles: list[dict[str, Any]] = []

    if id_list:
        summary = ncbi_get(
            "esummary.fcgi",
            {
                "db": "pubmed",
                "id": ",".join(id_list),
                "retmode": "json",
            },
        )
        result_map = summary.get("result", {})
        for pmid in id_list:
            raw = result_map.get(str(pmid), {})
            authors = [item.get("name", "") for item in raw.get("authors", []) if item.get("name")]
            articles.append(
                {
                    "pmid": str(pmid),
                    "title": raw.get("title", ""),
                    "journal": raw.get("fulljournalname", ""),
                    "pubdate": raw.get("pubdate", ""),
                    "authors": authors,
                    "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                }
            )

    return {
        "query": query,
        "total_count": count,
        "articles": articles,
    }


def list_tools() -> dict[str, Any]:
    return {
        "tools": [
            {
                "name": "search_pubmed",
                "description": "Search PubMed for the most relevant biomedical research papers.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "PubMed search query"},
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum number of articles to return",
                            "minimum": 1,
                            "maximum": 20,
                        },
                    },
                    "required": ["query"],
                },
            }
        ]
    }


def success_response(request_id: Any, result: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}) + "\n")
    sys.stdout.flush()


def error_response(request_id: Any, code: int, message: str) -> None:
    sys.stdout.write(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": code, "message": message},
            }
        )
        + "\n"
    )
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = message.get("method")
        request_id = message.get("id")

        if method == "initialize":
            success_response(
                request_id,
                {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "pubmed-mcp-server", "version": "0.1.0"},
                    "capabilities": {"tools": {}},
                },
            )
            continue

        if method == "notifications/initialized":
            continue

        if method == "tools/list":
            success_response(request_id, list_tools())
            continue

        if method == "tools/call":
            params = message.get("params", {})
            tool_name = params.get("name")
            arguments = params.get("arguments", {})
            try:
                if tool_name != "search_pubmed":
                    raise ValueError(f"Unknown tool '{tool_name}'")
                result = search_pubmed(arguments)
                success_response(
                    request_id,
                    {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=True)}]},
                )
            except ValueError as exc:
                error_response(request_id, -32602, str(exc))
            except error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                error_response(request_id, -32000, f"PubMed API error {exc.code}: {detail}")
            except error.URLError as exc:
                error_response(request_id, -32001, f"PubMed API connection failed: {exc.reason}")
            except Exception as exc:
                error_response(request_id, -32002, f"Unexpected PubMed server error: {exc}")
            continue

        if request_id is not None:
            error_response(request_id, -32601, f"Method '{method}' not found")


if __name__ == "__main__":
    main()
