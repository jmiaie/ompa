# MCP Server

OMPA exposes all of its capabilities as an MCP (Model Context Protocol) server with 24 tools. This lets Claude Desktop, Cursor, Windsurf, and any MCP-compatible client access your vault directly — no code changes needed.

## Setup

### Claude Desktop

```bash
claude mcp add ompa -- python -m ompa.mcp_server
```

Or add manually to `~/.claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ompa": {
      "command": "python",
      "args": ["-m", "ompa.mcp_server"],
      "env": {
        "OMPA_VAULT_PATH": "/path/to/your/vault"
      }
    }
  }
}
```

Restart Claude Desktop. OMPA tools appear in the tool list automatically.

### Cursor

Add to `.cursor/mcp.json` in your project root:

```json
{
  "mcpServers": {
    "ompa": {
      "command": "python",
      "args": ["-m", "ompa.mcp_server"],
      "env": {
        "OMPA_VAULT_PATH": "${workspaceFolder}/.ompa-vault"
      }
    }
  }
}
```

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OMPA_VAULT_PATH` | `.` (current directory) | Path to the vault |
| `OMPA_VAULT_ROOTS` | unset | Extra directories under which a tool call may name a vault, separated by `:` (`;` on Windows). Needed for dual-vault calls and for serving several vaults |
| `OMPA_INDEX_REFRESH_SECONDS` | `1` | How often, at most, a search checks the vault for notes edited outside OMPA. `0` checks on every search |
| `OMPA_ENABLE_SEMANTIC` | `false` | Enable semantic search |
| `OMPA_AGENT_NAME` | `agent` | Agent name for session hooks |

### Which directories the server will open

Tool arguments come from the model, so the server does not let a tool call choose
an arbitrary directory. It opens its default vault (`OMPA_VAULT_PATH`, or the
working directory when unset) and any directory under `OMPA_VAULT_ROOTS`. A
`vault_path`, `shared_vault_path` or `personal_vault_path` outside those returns
`{"error": "Invalid vault_path"}` and creates nothing. Paths are compared after
resolving symlinks, and a filesystem root is never accepted.

### How fresh search results are

Notes written through OMPA (`ao_write`, hooks) are searchable at once. Notes you
edit in another program are picked up by a check that walks the vault and
re-indexes what changed. The server runs that check before a search if it has
not run in the last `OMPA_INDEX_REFRESH_SECONDS` (default 1), so an outside edit
can take up to that long to appear. Before 1.2.1 the check ran before every
search, which on a 10,000-note vault made the call about four times as long as
the search itself. Set the variable to `0` to get that behaviour back.

## Available tools

| Tool | Description |
|------|-------------|
| `ao_session_start` | Inject full memory context (~2K tokens) |
| `ao_classify` | Classify and route a message to the right vault folder |
| `ao_search` | Semantic search across the vault |
| `ao_kg_query` | Query knowledge graph for an entity |
| `ao_kg_add` | Add a triple (subject, predicate, object) to the KG |
| `ao_kg_stats` | Knowledge graph statistics |
| `ao_palace_wings` | List all palace wings |
| `ao_palace_rooms` | List rooms in a wing |
| `ao_palace_tunnel` | Create or traverse a cross-wing tunnel |
| `ao_validate` | Validate vault structure and note frontmatter |
| `ao_wrap_up` | Session summary and persist to vault |
| `ao_status` | Vault health status |
| `ao_orphans` | Detect orphaned notes (no wikilinks) |
| `ao_init` | Initialize a new vault |
| `ao_stop` | Clean session shutdown |

## Usage in Claude Desktop

Once connected, Claude can use OMPA tools naturally in conversation:

> "Start a memory session for this project"
> → Claude calls `ao_session_start`, injects your vault context

> "Remember that we decided to use Postgres for the main database"
> → Claude calls `ao_classify` (DECISION), then saves to vault

> "What database decisions have we made?"
> → Claude calls `ao_search` with query "database decisions"

## Running the server manually

```bash
# Start MCP server directly (for testing)
python -m ompa.mcp_server

# With a specific vault
OMPA_VAULT_PATH=/path/to/vault python -m ompa.mcp_server

# With semantic search enabled
OMPA_ENABLE_SEMANTIC=true python -m ompa.mcp_server
```

The server communicates over stdio (standard MCP transport).
