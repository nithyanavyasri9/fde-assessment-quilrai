# FDE Assessment - MCP & LLM Security Gateways

**Role:** Forward Deployed Engineer / AI Solutions Engineer  
**Company:** QuilrAI  
**Stack:** Python, FastAPI, MCP SDK v2, SQLite, Pydantic, httpx2, uvicorn  
**Total Tests:** 106 passing across 4 tasks

---

## Tasks at a Glance

| Task | What I Built | Tests |
|------|-------------|-------|
| Task 1 | Custom MCP Server | 16/16 |
| Task 2 | MCP Security Gateway Proxy | 31/31 |
| Task 3 | LLM Streaming PII Redaction | 26/26 |
| Task 4 | Rate Limiting and Model Fallback Router | 33/33 |

---

## Task 1 - Custom MCP Server

**What the assessment asked for:**
- Two tools: `get_customer_record` (input: customer_id as CUST-XXXXX) and `trigger_refund` (inputs: customer_id, amount, reason)
- Strict Pydantic validation with standard JSON-RPC error codes
- stdio transport where stdout is JSON-RPC only and all logs go to stderr

**What I built on top of that:**

A third tool `get_metrics` that shows live call counts, error rates, and average response time per tool. This was not required but shows how the server is actually performing.

A custom error code system instead of generic errors. Every failure has a specific code like CUST_001 (customer not found), CUST_002 (bad format), REFUND_001 (bad amount), REFUND_002 (exceeds balance), REFUND_003 (duplicate within 60s), REFUND_004 (short or filler reason). These make errors machine readable not just human readable.

A JSON audit log that writes every tool call to audit.log with timestamp, inputs, outcome and error code.

A sliding window rate limiter that blocks more than 10 calls per minute per tool.

Extra business rules on trigger_refund that were not in the spec:
- Refund amount cannot exceed the customer balance
- Hard cap of $1000 per request
- Same customer cannot be refunded twice within 60 seconds
- Vague reasons like "test refund" get rejected

**Validation cases covered:**

| Input | What happens |
|-------|-------------|
| CUST-ABC | Rejected - letters not digits |
| CUST-1234 | Rejected - only 4 digits |
| 00001 | Rejected - missing CUST- prefix |
| CUST-99999 | Rejected - customer not found |
| amount = -10 | Rejected - must be positive |
| amount = 0 | Rejected - must be positive |
| amount = 1001 | Rejected - over $1000 cap |
| reason = "Too short" | Rejected - under 10 chars |
| reason = "test refund" | Rejected - filler text |
| amount > balance | Rejected - exceeds balance |
| same customer in 60s | Rejected - duplicate |

**How to run:**
```bash
cd "Task 1 Custom MCP Server"
pip install -r requirements.txt
python test_server.py
python server.py
python server.py 2>$null    # stdout must be completely silent
```

---

## Task 2 - MCP Security Gateway Proxy

**What the assessment asked for:**
- Read Authorization: Bearer token header and extract role (admin or viewer)
- Forward tools/list requests transparently
- Block tools/call requests where the tool name starts with admin_ if the role is not admin, return -32001 without calling downstream

**What I built on top of that:**

Better token validation. The spec only says to check the role. I also check if the header is missing, if the scheme is wrong (Basic instead of Bearer), if the format is malformed, and if the token is unknown. Each failure gets its own error message.

Unknown methods return -32601 instead of being forwarded silently. The spec only mentions tools/list and tools/call. Any other method hitting the gateway now gets rejected with the right error code instead of being passed through blindly.

Downstream errors are cleaned up before reaching the caller. If the downstream server times out or is unreachable, the caller gets a clean -32000 message with no Python tracebacks, no internal IP addresses, nothing that exposes the system internals.

A structured audit log (gateway_audit.log) that records role, method, tool, outcome, and timestamp for every single request.

A /health endpoint for monitoring.

**Error codes used:**

| Code | When it fires |
|------|--------------|
| -32001 | Missing token, bad token, viewer calling admin tool |
| -32601 | Unknown method - never forwarded |
| -32000 | Downstream timeout or unreachable |

**How to run:**
```bash
cd "Task 2 MCP Gateway"
pip install -r requirements.txt
python test_gateway.py                                       # 31/31 unit tests
python mock_mcp_server.py                                    # terminal 1
python gateway.py                                            # terminal 2
powershell -ExecutionPolicy Bypass -File .\run_tests.ps1    # 11/11 live tests
```

---

## Task 3 - LLM Gateway Streaming PII Redaction

**What the assessment asked for:**
- Proxy LLM requests and stream the response back
- Detect and redact emails, SSNs, and credit card numbers in real time
- Keep the stream responsive without buffering the full response

**Why this task is the hardest:**

Tasks 1, 2, and 4 are request and response. The data is complete when you process it. Task 3 is different. You are working on a live stream where the data arrives in small pieces and you never have the full picture. You cannot go back and fix something you already sent. Every millisecond you hold a chunk makes the user experience worse.

**The main problem most implementations miss:**

A naive regex on each chunk fails silently when PII spans two chunks:
```
chunk 1:  "email me at john.sm"     - no match
chunk 2:  "ith@example.com tonight" - no match
result:   john.smith@example.com leaked
```

**How I solved it:**

A rolling overlap buffer. Instead of redacting each chunk independently, I hold back the last 50 characters of every chunk. When the next chunk arrives, I redact the safe zone (everything except the last 50 chars) and pass it through. The 50-char overlap means any PII pattern split across a boundary will always be caught together in one redact call.

Memory stays at O(1). Only 50 characters are held at any point regardless of how long the response is.

**What I built on top of the requirements:**

Phone number redaction as a fourth PII type covering (555) 123-4567, 555.123.4567, and +1-555-123-4567.

Named tags instead of generic [REDACTED]. The caller sees [REDACTED-EMAIL], [REDACTED-SSN], [REDACTED-CC], or [REDACTED-PHONE] so they know what type of data was scrubbed.

A redaction counter that tracks how many of each PII type were caught per stream session.

Regex patterns compiled once at module load, not on every chunk.

Patterns ordered by specificity so SSN is matched before phone to avoid partial overlap issues.

An end-of-stream safety flush that guarantees the buffer is always released even if the [DONE] signal is missing.

**PII patterns covered:**

| Type | Tag | Example |
|------|-----|---------|
| Email | [REDACTED-EMAIL] | john.smith@example.com |
| SSN | [REDACTED-SSN] | 123-45-6789 |
| Credit card | [REDACTED-CC] | 4111 1111 1111 1111 |
| Phone | [REDACTED-PHONE] | (555) 123-4567 |

**Fixes made during development:**

| Problem | Fix |
|---------|-----|
| MCP SDK v2 changed FastMCP to MCPServer | Updated import to mcp.server.mcpserver |
| Overlap buffer too small for long emails | Increased OVERLAP_SIZE from 40 to 50 |
| [DONE] detection missing some variations | Changed to check if [DONE] is anywhere in the line |
| Buffer not flushed if stream ends without [DONE] | Added safety flush after the async loop ends |
| Mock server chunks too small to test boundaries | Resized chunks to around 30 chars |
| PowerShell test reassembling split PII | Rewrote test to check individual chunks not joined text |

**How to run:**
```bash
cd "Task 3 PII Gateway"
pip install -r requirements.txt
python test_gateway.py                                       # 26/26 unit tests
python mock_llm_server.py                                    # terminal 1
python gateway.py                                            # terminal 2
powershell -ExecutionPolicy Bypass -File .\run_tests.ps1    # 7/7 live stream checks
```

---

## Task 4 - Rate Limiting and Model Fallback Router

**What the assessment asked for:**
- Token-aware sliding window rate limiter at 50,000 tokens per minute per tenant
- Failover to a backup model if the primary returns 429 or times out after 3000ms
- Clean error responses with no internal details leaked
- SQLite on disk for storage

**What I built on top of that:**

SQLite WAL mode so readers never block writers. Important when multiple requests hit the database at the same time.

BEGIN IMMEDIATE transactions to prevent a race condition. Without this, two requests arriving at the same time can both read 49,000 tokens used, both pass the check, and both insert their tokens. The limit gets bypassed silently. BEGIN IMMEDIATE makes the second request wait until the first one commits, then it reads the updated total and gets blocked correctly.

asyncio.wait_for for the 3000ms timeout. The httpx timeout parameter closes the connection but does not cancel the Python coroutine. asyncio.wait_for actually cancels the task when time runs out.

A Retry-After header on 429 responses that tells the client exactly how many seconds to wait before trying again.

A _gateway field in fallback responses so the caller knows the backup model was used and why, without seeing any internal URLs.

A /admin/stats endpoint showing live token usage per tenant and a /admin/stats/{tenant} endpoint for individual tenant details.

A structured audit log (router_audit.log) recording every routing decision with tenant, tokens, decision type, model used, and response time.

**Fallback behavior:**

| Primary result | What happens |
|---------------|-------------|
| Success | Returns primary response |
| 429 Too Many Requests | Tries fallback model |
| Times out at 3000ms | Tries fallback model |
| Unreachable | Tries fallback model |
| Both fail | Returns 503 with clean error message |

**How to run:**
```bash
cd "Task 4 Rate Limiter"
pip install -r requirements.txt
python test_router.py                                        # 33/33 unit tests
python mock_llm_servers.py                                   # terminal 1 (starts primary + fallback)
python router.py                                             # terminal 2
powershell -ExecutionPolicy Bypass -File .\run_tests.ps1    # live HTTP tests
```

---

## Test Results

| Task | Unit Tests | Live Tests |
|------|-----------|------------|
| Task 1 - MCP Server | 16/16 | STDIO isolation verified |
| Task 2 - MCP Gateway | 31/31 | 11/11 live HTTP |
| Task 3 - PII Redaction | 26/26 | 7/7 live stream |
| Task 4 - Rate Limiter | 33/33 | Live HTTP verified |
| **Total** | **106/106** | |

---

## Project Structure

```
fde-assessment-quilrai/
├── README.md
├── Task 1 Custom MCP Server/
│   ├── server.py
│   ├── test_server.py
│   └── requirements.txt
├── Task 2 MCP Gateway/
│   ├── gateway.py
│   ├── mock_mcp_server.py
│   ├── test_gateway.py
│   ├── run_tests.ps1
│   └── requirements.txt
├── Task 3 PII Gateway/
│   ├── gateway.py
│   ├── mock_llm_server.py
│   ├── test_gateway.py
│   ├── run_tests.ps1
│   └── requirements.txt
└── Task 4 Rate Limiter/
    ├── router.py
    ├── mock_llm_servers.py
    ├── test_router.py
    ├── run_tests.ps1
    └── requirements.txt
```

---

## Port Reference

| Service | Port |
|---------|------|
| Task 2 Gateway | 9000 |
| Task 2 Mock MCP Server | 9001 |
| Task 3 Gateway | 9200 |
| Task 3 Mock LLM | 9100 |
| Task 4 Router | 9300 |
| Task 4 Mock Primary LLM | 9400 |
| Task 4 Mock Fallback LLM | 9401 |
