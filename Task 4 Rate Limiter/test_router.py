"""
Test suite for Task 4 — Rate-Limiting & Fallback Router.
Tests rate limiter logic directly — no servers needed.

Run: python test_router.py
"""

import sys
import asyncio
import os
import json
import time
from unittest.mock import AsyncMock, patch, MagicMock

def log(msg): print(msg, file=sys.stderr)


async def run_tests():
    # Clean state
    for f in ["rate_limiter.db", "router_audit.log"]:
        if os.path.exists(f): os.remove(f)

    from router import TokenRateLimiter, estimate_tokens, gateway_error, completions
    from router import RATE_LIMIT_TOKENS, RATE_LIMIT_WINDOW

    PASS, FAIL = "✓ PASS", "✗ FAIL"
    results = []

    def expect(label, condition, detail=""):
        if condition:
            results.append((PASS, label))
        else:
            results.append((FAIL, f"{label}{' -- ' + detail if detail else ''}"))

    log("\n=== Task 4 Rate-Limiting & Fallback Router Tests ===\n")

    # ── SQLite rate limiter ───────────────────────────────────────────────────
    log("-- SQLite Token Rate Limiter --")

    limiter = TokenRateLimiter("test_rate_limiter.db")

    # Allow within limit
    allowed, used, _ = limiter.check_and_record("tenant-A", 1000)
    expect("First request allowed", allowed, f"used={used}")

    # Allow cumulative within limit
    allowed, used, _ = limiter.check_and_record("tenant-A", 1000)
    expect("Second request allowed (cumulative)", allowed, f"used={used}")
    expect("Usage accumulates correctly", used == 2000, f"got {used}")

    # Tenant isolation — tenant-B unaffected by tenant-A
    allowed, used, _ = limiter.check_and_record("tenant-B", 10000)
    expect("Tenant B isolated from Tenant A", allowed and used == 10000)

    # Hit the limit
    allowed, used, reset = limiter.check_and_record("tenant-A", RATE_LIMIT_TOKENS)
    expect("Request blocked when limit exceeded", not allowed,
        f"allowed={allowed} used={used}")

    # Reset timestamp in future
    expect("Reset timestamp is in the future", reset > time.time(),
        f"reset={reset} now={time.time()}")

    # Stats endpoint
    stats = limiter.get_stats("tenant-A")
    expect("Stats returns correct tenant", stats["tenant"] == "tenant-A")
    expect("Stats shows tokens used", stats["tokens_used"] == 2000, f"got {stats['tokens_used']}")
    expect("Stats shows tokens remaining", stats["tokens_remaining"] == RATE_LIMIT_TOKENS - 2000)
    expect("Stats shows limit", stats["tokens_limit"] == RATE_LIMIT_TOKENS)

    # Clean up test db — delete all connections first (required on Windows)
    del limiter
    import gc; gc.collect()
    await asyncio.sleep(0.1)
    for fname in ["test_rate_limiter.db", "test_rate_limiter.db-wal", "test_rate_limiter.db-shm"]:
        if os.path.exists(fname):
            try: os.remove(fname)
            except: pass

    # ── Token estimation ──────────────────────────────────────────────────────
    log("-- Token Estimation --")

    body_small = {"messages": [{"role": "user", "content": "Hello"}], "max_tokens": 100}
    body_large = {"messages": [{"role": "user", "content": "A" * 400}], "max_tokens": 500}

    expect("Small message estimates > 0 tokens", estimate_tokens(body_small) > 0)
    expect("Large message estimates more tokens than small",
        estimate_tokens(body_large) > estimate_tokens(body_small))
    expect("Empty body returns > 0 tokens", estimate_tokens({}) > 0)

    # ── Error response format ─────────────────────────────────────────────────
    log("-- Error Response Format --")

    err = gateway_error("rate_limit_exceeded", "Too many tokens", status=429, retry_after=30)
    body = json.loads(err.body)
    expect("Error has correct status 429", err.status_code == 429)
    expect("Error body has error.code", body["error"]["code"] == "rate_limit_exceeded")
    expect("Error body has error.message", "Too many tokens" in body["error"]["message"])
    expect("Error body has error.type", body["error"]["type"] == "gateway_error")
    expect("Retry-After header present", err.headers.get("retry-after") == "30")
    expect("No stack trace in error body", "traceback" not in json.dumps(body).lower())
    expect("No internal URL in error body", "127.0.0.1" not in json.dumps(body))

    # ── Full router simulation ────────────────────────────────────────────────
    log("-- Router Request Simulation --")

    def make_request(tenant="tenant-test", content="Hello world"):
        body = {"messages": [{"role": "user", "content": content}], "max_tokens": 100}
        req = MagicMock()
        req.headers = {"Authorization": f"Bearer {tenant}"}
        req.json = AsyncMock(return_value=body)
        return req

    success_response = {
        "id": "test-001",
        "model": "primary",
        "choices": [{"message": {"role": "assistant", "content": "Hi!"}}]
    }

    # Primary succeeds
    with patch("router.call_upstream", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = (success_response, None)
        req = make_request("tenant-sim-1")
        resp = await completions(req)
        body = json.loads(resp.body)
        expect("Primary success returns result", "choices" in body)
        expect("Primary success has no error key", "error" not in body)

    # Primary returns 429 → fallback succeeds
    fallback_response = {**success_response, "model": "fallback"}
    with patch("router.call_upstream", new_callable=AsyncMock) as mock_call:
        mock_call.side_effect = [
            (None, "429"),                    # primary fails with 429
            (fallback_response, None),         # fallback succeeds
        ]
        req = make_request("tenant-sim-2")
        resp = await completions(req)
        body = json.loads(resp.body)
        expect("Fallback used on primary 429", body.get("_gateway", {}).get("fallback") is True)
        expect("Fallback response has choices", "choices" in body)

    # Primary timeout → fallback succeeds
    with patch("router.call_upstream", new_callable=AsyncMock) as mock_call:
        mock_call.side_effect = [
            (None, "timeout"),
            (fallback_response, None),
        ]
        req = make_request("tenant-sim-3")
        resp = await completions(req)
        body = json.loads(resp.body)
        expect("Fallback used on primary timeout", body.get("_gateway", {}).get("fallback") is True)

    # Both fail → 503
    with patch("router.call_upstream", new_callable=AsyncMock) as mock_call:
        mock_call.side_effect = [
            (None, "timeout"),
            (None, "unreachable"),
        ]
        req = make_request("tenant-sim-4")
        resp = await completions(req)
        body = json.loads(resp.body)
        expect("Both fail returns 503", resp.status_code == 503)
        expect("503 error code is upstream_unavailable",
            body["error"]["code"] == "upstream_unavailable")
        expect("503 has no internal URLs", "127.0.0.1" not in json.dumps(body))

    # Rate limited request
    with patch("router.limiter") as mock_limiter:
        mock_limiter.check_and_record.return_value = (False, 50000, time.time() + 30)
        req = make_request("tenant-sim-5", "x" * 1000)
        resp = await completions(req)
        body = json.loads(resp.body)
        expect("Rate limited returns 429", resp.status_code == 429)
        expect("Rate limited has Retry-After header", resp.headers.get("retry-after") is not None)
        expect("Rate limit error code correct",
            body["error"]["code"] == "rate_limit_exceeded")
        expect("Rate limit message mentions token count",
            "50,000" in body["error"]["message"])

    # ── Audit log ─────────────────────────────────────────────────────────────
    log("-- Audit Log --")
    try:
        with open("router_audit.log", encoding="utf-8") as f:
            entries = [json.loads(l) for l in f if l.strip()]
        has_allowed    = any(e["decision"] == "allowed" for e in entries)
        has_fallback   = any(e["decision"] == "fallback" for e in entries)
        has_rate_limit = any(e["decision"] == "rate_limited" for e in entries)
        has_error      = any(e["decision"] == "error" for e in entries)
        has_ts         = all("timestamp" in e for e in entries)
        expect(f"router_audit.log -- {len(entries)} entries, all decision types present",
            has_allowed and has_fallback and has_rate_limit and has_error and has_ts)
    except Exception as e:
        results.append((FAIL, f"router_audit.log -- {e}"))

    # ── Print results ─────────────────────────────────────────────────────────
    log("")
    passed = sum(1 for r in results if r[0].startswith("✓"))
    for status, label in results:
        log(f"  {status}  {label}")
    log(f"\n  {passed}/{len(results)} tests passed")
    if passed == len(results):
        log("\n  All tests passed! Task 4 complete.\n")
    else:
        log("\n  Some tests failed -- check above.\n")

    log("-- To test live --")
    log("  Terminal 1: python mock_llm_servers.py")
    log("  Terminal 2: python router.py")
    log("  Terminal 3: powershell -ExecutionPolicy Bypass -File .\\run_tests.ps1\n")


if __name__ == "__main__":
    asyncio.run(run_tests())