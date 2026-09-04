# Task 1 — Custom MCP Server

## Setup

```bash
cd task1_mcp_server
pip install -r requirements.txt
```

## Run the Server

```bash
python server.py
```

The server listens on **stdin** for JSON-RPC messages and replies on **stdout**.
All logs go to **stderr** — this is intentional and required by the assessment.

## Test Logic (without a running MCP client)

```bash
python test_server.py
```

All test output goes to stderr (correct behavior).

## Verify STDIO Isolation

This is the #1 thing assessors check:

```bash
# Redirect stderr to /dev/null — stdout should be completely silent
# until you send a JSON-RPC message via stdin
python server.py 2>/dev/null | cat
```

If you see ANY output before sending a message → you have a stdout leak → fix it.

## Tools Exposed

### `get_customer_record`
| Parameter | Type | Validation |
|-----------|------|------------|
| customer_id | string | Must match `CUST-XXXXX` (5 digits) |

**Error cases:**
- `CUST-ABC` → `-32602 InvalidParams` (bad format)
- `CUST-99999` → `-32000 InternalError` (not found)

### `trigger_refund`
| Parameter | Type | Validation |
|-----------|------|------------|
| customer_id | string | Must match `CUST-XXXXX` |
| amount | float | Must be > 0 |
| reason | string | Must be ≥ 10 characters |

**Error cases:**
- Bad customer_id format → `-32602`
- amount ≤ 0 → `-32602`
- reason < 10 chars → `-32602`
- Customer not found → `-32000`

## JSON-RPC Error Code Mapping

| Code | Meaning | When used |
|------|---------|-----------|
| -32602 | Invalid Params | Input fails Pydantic validation |
| -32000 | Server Error | Business logic failure (customer not found, etc.) |
| -32601 | Method Not Found | Unknown tool name called |

## Key Design Decisions

1. **Pydantic v2 models** with `@field_validator` for custom regex checks
2. **All logging via `logging` module pointed at `sys.stderr`** — zero `print()` calls
3. **`stdio_server()` from the MCP SDK** manages the transport layer
4. **Validation before business logic** — schema errors caught first, then DB/API errors
