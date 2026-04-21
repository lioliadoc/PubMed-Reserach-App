#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import textwrap
import uuid
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import error, request


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DB_PATH = BASE_DIR / "app_data.sqlite3"
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.4-mini")
MCP_COMMAND = [sys.executable, str(BASE_DIR / "pubmed_mcp_server.py")]

PROMPT_ANALYZER = textwrap.dedent(
    """
    You validate whether a user prompt is suitable for PubMed research search.
    Return JSON only with this exact shape:
    {
      "is_relevant": boolean,
      "normalized_question": string,
      "pubmed_query": string,
      "reason": string
    }

    Rules:
    - is_relevant must be true only when the prompt is a biomedical, clinical, public health,
      life sciences, pharmacy, or medical research topic that PubMed could plausibly contain.
    - If not relevant, pubmed_query must be an empty string and reason must explain the issue briefly.
    - If relevant, rewrite the topic into a concise PubMed-friendly query with key terms only.
    - Do not include markdown fences or commentary.
    """
).strip()

RESULT_SUMMARIZER = textwrap.dedent(
    """
    You summarize PubMed search results for a user.
    Return JSON only with this shape:
    {
      "summary": string
    }

    Keep the summary to 2 short sentences. Mention the search topic, how many results were found,
    and the main themes suggested by the top paper titles. Do not claim medical certainty.
    """
).strip()


class AppError(Exception):
    pass


def log_event(event: str, **fields: Any) -> None:
    payload = " ".join(f"{key}={value!r}" for key, value in fields.items())
    print(f"[app] {event}" + (f" {payload}" if payload else ""), flush=True)


def init_db() -> None:
    log_event("db.init.start", db_path=str(DB_PATH))
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS search_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                original_prompt TEXT NOT NULL,
                normalized_question TEXT,
                pubmed_query TEXT,
                status TEXT NOT NULL,
                message TEXT NOT NULL,
                result_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
    log_event("db.init.done", db_path=str(DB_PATH))


def save_search(response: dict[str, Any], original_prompt: str) -> None:
    log_event(
        "db.save_search.start",
        status=response.get("status"),
        result_count=response.get("result_count", 0),
    )
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """
            INSERT INTO search_history (
                original_prompt,
                normalized_question,
                pubmed_query,
                status,
                message,
                result_count
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                original_prompt,
                str(response.get("normalized_question", "")).strip(),
                str(response.get("pubmed_query", "")).strip(),
                str(response.get("status", "unknown")),
                str(response.get("message", "")),
                int(response.get("result_count", 0) or 0),
            ),
        )
    log_event("db.save_search.done", status=response.get("status"))


def recent_searches(limit: int = 10) -> list[dict[str, Any]]:
    log_event("db.recent_searches.start", limit=limit)
    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT created_at, original_prompt, normalized_question, pubmed_query, status, result_count
            FROM search_history
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    result = [dict(row) for row in rows]
    log_event("db.recent_searches.done", count=len(result))
    return result


def openai_json(system_text: str, user_text: str) -> dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise AppError("OPENAI_API_KEY is not set")

    payload = {
        "model": OPENAI_MODEL,
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": system_text}]},
            {"role": "user", "content": [{"type": "input_text", "text": user_text}]},
        ],
    }
    log_event("openai.request.start", model=OPENAI_MODEL, user_text_length=len(user_text))
    req = request.Request(
        f"{OPENAI_BASE_URL.rstrip('/')}/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=60) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        log_event("openai.request.http_error", status=exc.code)
        raise AppError(f"OpenAI API error {exc.code}: {detail}") from exc
    except error.URLError as exc:
        log_event("openai.request.url_error", reason=exc.reason)
        raise AppError(f"OpenAI API connection failed: {exc.reason}") from exc

    text = extract_openai_text(raw).strip()
    if not text:
        log_event("openai.request.empty_text")
        raise AppError("OpenAI API response did not include readable text output")

    try:
        parsed = json.loads(text)
        log_event("openai.request.done", keys=list(parsed.keys()))
        return parsed
    except json.JSONDecodeError as exc:
        log_event("openai.request.invalid_json", preview=text[:120])
        raise AppError(f"OpenAI API returned non-JSON text: {text!r}") from exc


def extract_openai_text(raw: dict[str, Any]) -> str:
    output_text = raw.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        log_event("openai.extract.output_text")
        return output_text

    parts: list[str] = []
    for item in raw.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text)

    if parts:
        log_event("openai.extract.output_parts", parts=len(parts))
        return "\n".join(parts)
    if raw.get("refusal"):
        log_event("openai.extract.refusal")
        raise AppError(f"OpenAI API refused the request: {raw['refusal']}")
    if raw.get("status") == "incomplete":
        log_event("openai.extract.incomplete")
        raise AppError(f"OpenAI API response was incomplete: {raw.get('incomplete_details')}")
    return ""


def call_mcp_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    log_event("mcp.call.start", tool=tool_name, arguments=arguments)
    init_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())
    payload = "\n".join(
        [
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": init_id,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "pubmed-research-mvp", "version": "0.1.0"},
                    },
                }
            ),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}),
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": arguments},
                }
            ),
            "",
        ]
    )

    try:
        proc = subprocess.Popen(
            MCP_COMMAND,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(BASE_DIR),
            env=os.environ.copy(),
        )
        stdout_data, stderr_data = proc.communicate(input=payload, timeout=45)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        log_event("mcp.call.timeout", tool=tool_name)
        raise AppError(f"MCP request timed out for tool '{tool_name}'") from exc

    rpc_response = None
    for line in stdout_data.splitlines():
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("id") == request_id:
            rpc_response = message

    if rpc_response is None:
        log_event("mcp.call.no_response", tool=tool_name, stderr=stderr_data.strip())
        raise AppError(f"MCP server did not return a usable response. stderr={stderr_data.strip()!r}")
    if "error" in rpc_response:
        log_event("mcp.call.error", tool=tool_name, error=rpc_response["error"])
        raise AppError(f"MCP error: {rpc_response['error']}")

    content = rpc_response.get("result", {}).get("content", [])
    if not content or not isinstance(content[0], dict) or not content[0].get("text"):
        log_event("mcp.call.invalid_content", tool=tool_name)
        raise AppError(f"MCP tool '{tool_name}' returned no readable content")
    result = json.loads(content[0]["text"])
    log_event("mcp.call.done", tool=tool_name, keys=list(result.keys()))
    return result


def summarize_results(prompt: str, papers: list[dict[str, Any]], total_count: int) -> str:
    log_event("summary.start", prompt_length=len(prompt), papers=len(papers), total_count=total_count)
    try:
        summary = openai_json(
            RESULT_SUMMARIZER,
            json.dumps(
                {
                    "prompt": prompt,
                    "total_count": total_count,
                    "top_papers": [
                        {
                            "title": paper.get("title", ""),
                            "journal": paper.get("journal", ""),
                            "pubdate": paper.get("pubdate", ""),
                        }
                        for paper in papers[:5]
                    ],
                },
                ensure_ascii=True,
            ),
        ).get("summary", "")
    except AppError:
        log_event("summary.fallback")
        summary = ""

    if isinstance(summary, str) and summary.strip():
        log_event("summary.done", source="openai")
        return summary.strip()
    if not papers:
        log_event("summary.done", source="fallback_empty")
        return f"PubMed returned 0 matching articles for '{prompt}'."
    log_event("summary.done", source="fallback_default")
    return f"PubMed returned {total_count} matching articles for '{prompt}'. The titles below are the most relevant matches."


def search(prompt: str) -> dict[str, Any]:
    log_event("search.start", prompt=prompt)
    cleaned_prompt = " ".join(prompt.split()).strip()
    if not cleaned_prompt:
        response = {
            "ok": False,
            "status": "invalid_input",
            "message": "Enter a biomedical research question or topic.",
        }
        save_search(response, cleaned_prompt)
        log_event("search.invalid.empty")
        return response

    analysis = openai_json(PROMPT_ANALYZER, cleaned_prompt)
    normalized_question = str(analysis.get("normalized_question", "")).strip()
    log_event("search.analysis.done", is_relevant=analysis.get("is_relevant"), normalized_question=normalized_question)

    if not analysis.get("is_relevant"):
        response = {
            "ok": False,
            "status": "invalid_input",
            "message": str(analysis.get("reason", "")).strip() or "The input is not relevant to PubMed.",
            "normalized_question": normalized_question,
        }
        save_search(response, cleaned_prompt)
        log_event("search.invalid.irrelevant", reason=response["message"])
        return response

    pubmed_query = str(analysis.get("pubmed_query", "")).strip()
    if not pubmed_query:
        log_event("search.invalid.missing_query")
        raise AppError("The AI validation step did not return a PubMed query")

    pubmed_result = call_mcp_tool("search_pubmed", {"query": pubmed_query, "max_results": 8})
    articles = pubmed_result.get("articles", [])
    result_count = int(pubmed_result.get("total_count", 0))
    log_event("search.pubmed.done", result_count=result_count, articles=len(articles))

    response = {
        "ok": True,
        "status": "success",
        "original_prompt": cleaned_prompt,
        "normalized_question": normalized_question,
        "pubmed_query": pubmed_query,
        "message": summarize_results(cleaned_prompt, articles, result_count),
        "result_count": result_count,
        "articles": articles,
    }
    save_search(response, cleaned_prompt)
    log_event("search.done", status=response["status"], result_count=result_count)
    return response


class AppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def do_GET(self) -> None:
        log_event("http.get", path=self.path)
        if self.path == "/":
            self.path = "/index.html"
            return super().do_GET()
        if self.path == "/healthz":
            return self.send_json({"ok": True}, HTTPStatus.OK)
        if self.path == "/api/history":
            return self.send_json({"ok": True, "items": recent_searches()}, HTTPStatus.OK)
        return super().do_GET()

    def do_POST(self) -> None:
        log_event("http.post", path=self.path)
        if self.path != "/api/search":
            return self.send_json({"ok": False, "message": "Not found"}, HTTPStatus.NOT_FOUND)

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
            log_event("http.post.payload", keys=list(payload.keys()))
        except (ValueError, json.JSONDecodeError):
            log_event("http.post.invalid_json")
            return self.send_json(
                {"ok": False, "message": "Request body must be valid JSON."},
                HTTPStatus.BAD_REQUEST,
            )

        try:
            response = search(str(payload.get("prompt", "")))
            status = HTTPStatus.OK if response.get("ok") else HTTPStatus.BAD_REQUEST
        except AppError as exc:
            log_event("http.post.app_error", error=str(exc))
            response = {"ok": False, "message": str(exc)}
            status = HTTPStatus.INTERNAL_SERVER_ERROR if "OPENAI_API_KEY is not set" in str(exc) else HTTPStatus.BAD_GATEWAY
        except Exception as exc:
            log_event("http.post.unexpected_error", error=str(exc))
            response = {"ok": False, "message": f"Unexpected server error: {exc}"}
            status = HTTPStatus.INTERNAL_SERVER_ERROR

        self.send_json(response, status)

    def send_json(self, payload: dict[str, Any], status: HTTPStatus) -> None:
        log_event("http.response", status=int(status), keys=list(payload.keys()))
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        sys.stdout.write("%s - - [%s] %s\n" % (self.client_address[0], self.log_date_time_string(), format % args))


def run() -> None:
    init_db()
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("127.0.0.1", port), AppHandler)
    log_event("server.start", host="127.0.0.1", port=port)
    print(f"Serving PubMed Research MVP at http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run()
