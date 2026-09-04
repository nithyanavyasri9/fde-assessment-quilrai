# \# FDE Assessment - MCP \& LLM Security Gateways

# 

# \*\*Role:\*\* Forward Deployed Engineer (FDE) / AI Solutions Engineer  

# \*\*Company:\*\* QuilrAI  

# \*\*Stack:\*\* Python · FastAPI · MCP SDK v2 · SQLite · Pydantic · httpx2 · uvicorn

# 

# \---

# 

# \## Overview

# 

# This assessment consists of 4 practical engineering tasks covering Model Context Protocol (MCP) servers, security proxy gateways, LLM streaming guardrails, and resilient model routing. Every task meets the assessment requirements and includes additional production-grade features beyond the baseline spec.

# 

# \---

# 

# \## Task 1 - Custom MCP Server

# 

# \*\*What was required:\*\*

# \- Build a runnable MCP server using the official Python SDK

# \- Expose two tools: `get\_customer\_record` (input: `customer\_id` as `CUST-XXXXX`) and `trigger\_refund` (inputs: `customer\_id`, `amount`, `reason`)

# \- Enforce strict input validation using Pydantic - reject invalid formats with standard JSON-RPC error codes

# \- Connect via stdio transport - stdout reserved for JSON-RPC only, all logs to stderr

# 

# \*\*What I built beyond the requirements:\*\*

# \- Third tool `get\_metrics` - returns live call counts, error rates, and average response time per tool

# \- Custom typed error hierarchy with machine-readable codes (`CUST\_001`, `CUST\_002`, `REFUND\_001–004`) instead of generic errors

# \- Structured JSON audit log (`audit.log`) - every tool call timestamped with inputs, outcome, and error code

# \- In-memory sliding window rate limiter - max 10 calls per minute per tool

# \- Business rules on `trigger\_refund`: balance check, duplicate protection (60s window), $1,000 cap per refund, filler reason detection

# 

# \*\*How to run:\*\*

# ```bash

# cd "Task 1 MCP Server"

# pip install -r requirements.txt

# python test\_server.py        # run all tests

# python server.py             # start the server

# python server.py 2>$null     # verify STDIO isolation (stdout must be silent)

# ```

# 

# \---

# 

# \## Task 2 - MCP Security Gateway Proxy

# 

# \*\*What was required:\*\*

# \- Build an HTTP/JSON-RPC reverse proxy sitting between an AI agent and a downstream MCP server

# \- Read `Authorization: Bearer <token>` header and extract user role (`admin` or `viewer`)

# \- Forward `tools/list` requests transparently

# \- For `tools/call`: if `params.name` starts with `admin\_` and role is not `admin`, return `-32001 Unauthorized Tool Call` without calling the downstream server

# 

# \*\*What I built beyond the requirements:\*\*

# \- Full token validation: checks header format, auth scheme (Bearer vs Basic), and token registry - distinct error message per failure type

# \- Unknown methods return `-32601` instead of being silently forwarded - prevents unintended method exposure

# \- Downstream error sanitization - timeouts and unreachable servers return clean `-32000` with no raw tracebacks or internal URLs

# \- Structured audit log (`gateway\_audit.log`) - role, method, tool, outcome, and timestamp on every request

# \- `/health` endpoint for ops monitoring

# \- Structured `error.data` field in blocked responses - includes tool name, role, and reason

# 

# \*\*How to run:\*\*

# ```bash

# cd "Task 2 MCP Gateway"

# pip install -r requirements.txt

# python test\_gateway.py                                        # unit tests (no servers needed)

# python mock\_mcp\_server.py                                     # terminal 1

# python gateway.py                                             # terminal 2

# powershell -ExecutionPolicy Bypass -File .\\run\_tests.ps1     # live HTTP tests

# ```

# 

# \---

# 

# \## Task 3 - LLM Gateway Streaming PII Redaction

# 

# \*\*What was required:\*\*

# \- Proxy LLM text generation requests and stream the response back to the client

# \- Intercept the chunk stream in real time and redact sensitive patterns (emails, SSNs, credit card numbers)

# \- Keep the stream responsive without buffering the full response - minimize Time To First Token (TTFT)

# 

# \*\*What I built beyond the requirements:\*\*

# \- Added phone number pattern (`\[REDACTED-PHONE]`) in addition to the required three

# \- Named redaction tags (`\[REDACTED-EMAIL]`, `\[REDACTED-SSN]`, `\[REDACTED-CC]`, `\[REDACTED-PHONE]`) instead of generic `\[REDACTED]` - tells the caller what type of PII was scrubbed

# \- \*\*Rolling overlap buffer\*\* - solves the cross-chunk boundary problem that naive implementations miss entirely:

# &#x20; - Naive regex fails when PII spans two chunks: `"john.sm"` + `"ith@example.com"`

# &#x20; - Solution: hold back the last 50 chars of each chunk as overlap - PII is always redacted in a single `\_redact()` call

# &#x20; - Memory is O(1): only 50 chars held regardless of response length

# \- Redaction audit counter - tracks how many of each PII type were caught per stream

# \- Regex patterns compiled once at module load - not recompiled per chunk

# \- Patterns ordered by specificity (SSN before phone) to prevent partial overlap matches

# 

# \*\*How to run:\*\*

# ```bash

# cd "Task 3 PII Gateway"

# pip install -r requirements.txt

# python test\_gateway.py                                        # unit tests (no servers needed)

# python mock\_llm\_server.py                                     # terminal 1

# python gateway.py                                             # terminal 2

# powershell -ExecutionPolicy Bypass -File .\\run\_tests.ps1     # live stream test

# ```

# 

# \---

# 

# \## Task 4 - Rate-Limiting \& Model Fallback Router

# 

# \*\*What was required:\*\*

# \- Token-aware sliding window rate limiter - maximum 50,000 tokens per minute per tenant API key

# \- Automatic failover to a secondary model if the primary returns `429` or times out after 3000ms

# \- Standardized error payload - no raw stack traces or internal implementation details in responses

# \- Use SQLite on disk for the database

# 

# \*\*What I built beyond the requirements:\*\*

# \- SQLite WAL mode (`PRAGMA journal\_mode=WAL`) - readers never block writers under concurrent load

# \- `BEGIN IMMEDIATE` transaction - prevents race condition where two simultaneous requests both read 49,000 tokens used, both pass the check, and both insert - silently exceeding the limit

# \- `asyncio.wait\_for` for timeout enforcement - actually cancels the coroutine on timeout (httpx timeout parameter alone does not)

# \- `Retry-After` header on `429` responses - tells the client exactly when their window resets

# \- `\_gateway` metadata field on fallback responses - flags to the caller that fallback was used and why, without exposing internal URLs

# \- `/admin/stats` endpoint - live per-tenant token usage dashboard (tokens used, remaining, requests in window)

# \- `/admin/stats/{tenant}` - per-tenant breakdown

# \- Structured audit log (`router\_audit.log`) - every routing decision recorded with elapsed time

# 

# \*\*How to run:\*\*

# ```bash

# cd "Task 4 Rate Limiter"

# pip install -r requirements.txt

# python test\_router.py                                         # unit tests (no servers needed)

# python mock\_llm\_servers.py                                    # terminal 1 (starts both primary + fallback)

# python router.py                                              # terminal 2

# powershell -ExecutionPolicy Bypass -File .\\run\_tests.ps1     # live HTTP tests

# ```

# 

# \---

# 

# \## Test Results

# 

# | Task | Unit Tests | Live Tests |

# |------|-----------|------------|

# | Task 1 - MCP Server | 16/16 | STDIO isolation verified |

# | Task 2 - MCP Gateway | 31/31 | 11/11 live HTTP |

# | Task 3 - PII Redaction | 26/26 | Live stream verified |

# | Task 4 - Rate Limiter | All passing | Live HTTP verified |

# 

# \---

# 

# \## Project Structure

# 

# ```

# fde-assessment-quilrai/

# ├── Task 1 MCP Server/

# │   ├── server.py              # MCP server - main implementation

# │   ├── test\_server.py         # unit test suite

# │   └── requirements.txt

# ├── Task 2 MCP Gateway/

# │   ├── gateway.py             # security gateway - main implementation

# │   ├── mock\_mcp\_server.py     # mock downstream MCP server for testing

# │   ├── test\_gateway.py        # unit test suite

# │   ├── run\_tests.ps1          # live HTTP test script

# │   └── requirements.txt

# ├── Task 3 PII Gateway/

# │   ├── gateway.py             # PII redaction gateway - main implementation

# │   ├── mock\_llm\_server.py     # mock LLM server with PII spanning chunks

# │   ├── test\_gateway.py        # unit test suite

# │   ├── run\_tests.ps1          # live stream test script

# │   └── requirements.txt

# └── Task 4 Rate Limiter/

# &#x20;   ├── router.py              # rate limiter + fallback router - main implementation

# &#x20;   ├── mock\_llm\_servers.py    # mock primary + fallback LLM servers

# &#x20;   ├── test\_router.py         # unit test suite

# &#x20;   ├── run\_tests.ps1          # live HTTP test script

# &#x20;   └── requirements.txt

# ```

