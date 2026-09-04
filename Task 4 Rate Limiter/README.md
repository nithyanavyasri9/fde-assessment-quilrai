# Task 4 - Rate-Limiting and Model Fallback Router

## Setup
pip install -r requirements.txt

## Run
Terminal 1 - mock LLM servers (primary + fallback):
python mock_llm_servers.py

Terminal 2 - router:
python router.py

## Test (no servers needed)
python test_router.py

## Live test (servers must be running)
powershell -ExecutionPolicy Bypass -File .\run_tests.ps1

## Rate Limiting
- 50,000 tokens per minute per tenant API key
- Token-aware: counts input tokens + max_tokens per request
- SQLite on disk (rate_limiter.db) - survives restarts
- WAL mode + BEGIN IMMEDIATE - concurrent-safe writes

## Fallback Routing
| Primary failure | Action |
|----------------|--------|
| Returns 429 | Failover to backup model |
| Times out 3000ms | Failover to backup model |
| Both fail | 503 upstream_unavailable |

## Endpoints
- POST /v1/chat/completions - main completion endpoint
- GET /admin/stats - live per-tenant token usage
- GET /admin/stats/{tenant} - per-tenant breakdown
- GET /health - health check
