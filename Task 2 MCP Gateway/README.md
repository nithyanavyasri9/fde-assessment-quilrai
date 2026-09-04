# Task 2 — MCP Security Gateway Proxy

## Architecture

```
AI Agent → [Gateway :9000] → [Downstream MCP Server :9001]
```

## Setup

```powershell
pip install -r requirements.txt
```

## Run

Open **two terminals**:

**Terminal 1 — start the mock downstream server:**
```powershell
python mock_mcp_server.py
```

**Terminal 2 — start the gateway:**
```powershell
python gateway.py
```

## Test (no servers needed)

```powershell
python test_gateway.py
```

## Test with curl (servers must be running)

```powershell
# viewer calling regular tool — ALLOWED
curl -X POST http://localhost:9000/mcp `
  -H "Authorization: Bearer token-viewer-001" `
  -H "Content-Type: application/json" `
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_customer_record"}}'

# viewer calling admin tool — BLOCKED
curl -X POST http://localhost:9000/mcp `
  -H "Authorization: Bearer token-viewer-001" `
  -H "Content-Type: application/json" `
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"admin_reset_key"}}'

# admin calling admin tool — ALLOWED
curl -X POST http://localhost:9000/mcp `
  -H "Authorization: Bearer token-admin-001" `
  -H "Content-Type: application/json" `
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"admin_reset_key"}}'
```

## Test Tokens

| Token | Role |
|-------|------|
| `token-admin-001` | admin |
| `token-viewer-001` | viewer |
| `token-viewer-002` | viewer |

## JSON-RPC Error Codes Used

| Code | Meaning |
|------|---------|
| -32700 | Parse error (bad JSON body) |
| -32601 | Method not found (unknown MCP method) |
| -32602 | Invalid params (missing tools/call name) |
| -32001 | Unauthorized tool call |
| -32000 | Internal error (downstream timeout/unreachable) |

## Differentiators

- Structured audit log (`gateway_audit.log`) — every request logged with role, tool, outcome
- Request with no/bad token rejected before touching downstream
- Unknown methods return -32601, never silently forwarded
- Downstream timeouts return clean -32000, no raw stack traces
- `/health` endpoint for ops monitoring
