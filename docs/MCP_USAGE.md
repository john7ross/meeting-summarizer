# Meeting archive over MCP — usage guide

**English** · [Русский](MCP_USAGE.ru.md)

`backend/mcp_server.py` exposes the processed meeting archive to any MCP-capable agent, so
the agent can look meetings up on its own instead of being handed a transcript.

It speaks **MCP over stdio**: newline-delimited JSON-RPC 2.0 on stdin/stdout, no SDK
required. Stdout carries the protocol only — diagnostics go to stderr.

## Register

Print ready-to-paste snippets for your client:

```
backend\python\python.exe backend\mcp_server.py --print-registration
```

It emits a CLI form and a JSON `mcpServers` block. The generic shape is a stdio server
launched by a command:

```json
{
  "mcpServers": {
    "meetings": {
      "command": "<path to python>",
      "args": ["<path to backend/mcp_server.py>"]
    }
  }
}
```

Consult your client's own documentation for where that config lives — this server does not
assume any particular host.

## Tools

| Tool | Arguments | Returns |
|---|---|---|
| `list_meetings` | `limit`, `project`, `only_with_summary` | Newest-first list: id, name, date, project, status, version counts |
| `get_transcript` | `meeting_id` (required), `max_chars` | Full transcript text |
| `get_summary` | `meeting_id` (required), `version` | Summary text (latest version unless `version` given) |
| `get_analysis` | `meeting_id` (required), `version`, `feature` | Analysis JSON, or just one feature |
| `search_transcripts` | `query` (required), `limit`, `context` | Literal, case-insensitive matches with excerpts |
| `search_knowledge` | `query` (required), `project`, `top_k` | Semantic matches from every local RAG catalog, with source labels |

`meeting_id` values come from `list_meetings`. Analysis features are `actionItems`,
`sentiment`, `category`, `keyTopics`, `risks`, `quotes`, `technologies`, `questions`,
`recommendations`, `followupQuestions`, `formalProtocol`.

## Typical flows

**Find what was decided about a topic.** `search_transcripts` (or `search_knowledge` when the
wording may differ) → take the `meeting_id` → `get_summary` for the conclusion, or
`get_analysis` with `feature: "actionItems"` for the follow-ups.

**Prepare for a recurring meeting.** `list_meetings` with the `project` filter →
`get_summary` of the last one → `get_analysis` with `feature: "followupQuestions"`.

**Audit commitments across meetings.** `list_meetings` with `only_with_summary: true`, then
`get_analysis` with `feature: "actionItems"` per meeting.

Prefer summaries and analysis over transcripts: a transcript can be tens of thousands of
words, so pull it only when the exact wording matters, and use `max_chars` to cap it.

## Errors

A tool that cannot answer returns an MCP tool error (`isError`) whose text says why —
`Meeting 999 not found`, `Meeting 222 has no summary`, `Unknown feature 'nope'; available:
[...]`. Unknown tool or method names return JSON-RPC errors (`-32602` / `-32601`). Nothing
crashes the server; it keeps serving.

## Notes

- Read-only. The server never modifies meetings, settings or artifacts.
- `search_knowledge` needs at least one catalog populated by desktop or web. It discovers
  `rag_knowledge_base`, `rag_data/u*`, `rag_shared/*` and the legacy root `rag_data`,
  searches every compatible store, deduplicates globally and returns
  `catalog`/`catalog_kind`. Incompatible embedding models do not abort the full request;
  their reasons appear in `catalogs_skipped`.
- MCP can read every local catalog in this installation and must therefore run only for a
  trusted user. The embedding provider is selected from each catalog's metadata; a
  cloud-backed catalog may require network access and an API key.
- The archive is read live from `config/history.json` and the artifact files — a meeting
  processed while the agent is connected shows up on the next `list_meetings`.
