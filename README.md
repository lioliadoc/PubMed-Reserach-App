# PubMed Research App

This project is a small full-stack application for exploring biomedical research in PubMed through a simpler search interface.

The user enters a natural-language topic or question in the browser. The backend checks whether the prompt is relevant to PubMed, generates a search query, retrieves matching papers, and returns a short summary together with the number of results and the top article titles. The application also stores recent searches in a local SQLite database.

## Features

- browser-based search form
- backend validation for biomedical / PubMed-relevant prompts
- PubMed search through a local MCP server
- concise response with:
  - normalized topic
  - generated PubMed query
  - result count
  - top matching papers
  - short summary
- recent search history stored in SQLite

## Requirements

- Python 3.10 or later
- internet access for external API calls
- an `OPENAI_API_KEY`

Optional environment variables:

- `OPENAI_MODEL`
  - defaults to `gpt-5.4-mini`
- `OPENAI_BASE_URL`
  - use this only if you want to point the app to a compatible proxy
- `NCBI_API_KEY`
  - optional PubMed / NCBI API key for higher rate limits
- `NCBI_EMAIL`
  - recommended when calling NCBI services

## Running the App

```bash
export OPENAI_API_KEY=your_key_here
export NCBI_EMAIL=email
python3 app.py
```

Open:

```text
http://127.0.0.1:8000/
```

Useful endpoints:

- `GET /healthz`
- `POST /api/search`
- `GET /api/history`

## Example Search Prompts

Valid prompt:

```text
What research does PubMed contain on GLP-1 receptor agonists for obesity?
```

Invalid prompt example:

```text
best beaches in Europe
```

## How It Works

1. The frontend sends the prompt to `POST /api/search`.
2. The backend validates and normalizes the prompt.
3. If the prompt is valid, the backend calls the local PubMed MCP server.
4. The MCP server queries PubMed and returns structured results.
5. The backend builds the final response and stores the search in SQLite.
6. The frontend renders the response and refreshes recent history.

## Database

The application uses a single SQLite table, `search_history`, to store:

- original prompt
- normalized question
- generated PubMed query
- status
- response message
- result count
- timestamp

To inspect the schema locally:

```bash
sqlite3 app_data.sqlite3
```

Then run:

```sql
.schema search_history
```


