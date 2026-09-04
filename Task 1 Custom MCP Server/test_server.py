"""
Test suite for enhanced Task 1 MCP Server.
Covers: validation, business rules, rate limiting, audit log, and metrics.
All output goes to stderr (correct for an MCP server).
Run: python test_server.py
"""
import sys
import asyncio
import os
import json
import time

def log(msg): print(msg, file=sys.stderr)

async def run_tests():
    # Reset state between test runs
    if os.path.exists("audit.log"):
        os.remove("audit.log")

    from server import (
        get_customer_record, trigger_refund, get_metrics,
        MOCK_CUSTOMERS, _last_refund_time, _rate_windows,
    )

    # Reset in-memory state
    MOCK_CUSTOMERS["CUST-00001"]["balance"] = 250.00
    MOCK_CUSTOMERS["CUST-00002"]["balance"] = 75.50
    _last_refund_time.clear()
    _rate_windows.clear()

    PASS, FAIL = "✓ PASS", "✗ FAIL"
    results = []

    def expect_success(label, fn, check=None):
        try:
            result = fn()
            if check and not check(result):
                results.append((FAIL, f"{label} — result check failed: {result!r}"))
            else:
                results.append((PASS, label))
        except Exception as e:
            results.append((FAIL, f"{label} — unexpected error: {e}"))

    def expect_error(label, fn, keyword=None, error_code=None):
        try:
            fn()
            results.append((FAIL, f"{label} — should have raised an error"))
        except Exception as e:
            msg = str(e)
            if keyword and keyword.lower() not in msg.lower():
                results.append((FAIL, f"{label} — wrong error message: {msg}"))
            elif error_code and error_code not in msg:
                results.append((FAIL, f"{label} — expected error code {error_code}, got: {msg}"))
            else:
                results.append((PASS, label))

    log("\n=== Task 1 Enhanced MCP Server Tests ===\n")

    # ── Baseline validation ──────────────────────────────────────────────────
    log("— Input Validation —")
    expect_success(
        "get_customer_record — valid ID returns data",
        lambda: get_customer_record("CUST-00001"),
        check=lambda r: "Alice Johnson" in r
    )
    expect_error("get_customer_record — rejects CUST-ABC",
        lambda: get_customer_record("CUST-ABC"), error_code="CUST_002")
    expect_error("get_customer_record — rejects missing prefix",
        lambda: get_customer_record("00001"), error_code="CUST_002")
    expect_error("get_customer_record — rejects 4-digit ID",
        lambda: get_customer_record("CUST-1234"), error_code="CUST_002")
    expect_error("get_customer_record — CUST-99999 not found",
        lambda: get_customer_record("CUST-99999"), error_code="CUST_001")

    # ── Refund validation ────────────────────────────────────────────────────
    log("— Refund Validation —")
    expect_error("trigger_refund — rejects negative amount",
        lambda: trigger_refund("CUST-00001", -10.0, "Item arrived broken and unusable"),
        error_code="REFUND_001")
    expect_error("trigger_refund — rejects zero amount",
        lambda: trigger_refund("CUST-00001", 0, "Item arrived broken and unusable"),
        error_code="REFUND_001")
    expect_error("trigger_refund — rejects reason < 10 chars",
        lambda: trigger_refund("CUST-00001", 10.0, "Too short"),
        error_code="REFUND_004")
    expect_success("trigger_refund — accepts reason of exactly 10 chars",
        lambda: trigger_refund("CUST-00001", 10.0, "Item broke x"))
    _last_refund_time.clear()  # reset duplicate guard after successful refund

    # ── Business rules ───────────────────────────────────────────────────────
    log("— Business Rules —")

    # Max refund cap
    expect_error("trigger_refund — rejects amount > $1000 cap",
        lambda: trigger_refund("CUST-00001", 1001.00, "Item arrived broken and unusable for use"),
        error_code="REFUND_001")

    # Balance check
    expect_error("trigger_refund — rejects amount exceeding balance ($75.50)",
        lambda: trigger_refund("CUST-00002", 100.00, "Item arrived broken and unusable for use"),
        error_code="REFUND_002")

    # Filler reason detection
    expect_error("trigger_refund — rejects filler reason 'test refund reason'",
        lambda: trigger_refund("CUST-00001", 10.0, "test refund reason"),
        error_code="REFUND_004")

    # Successful refund — also tests balance deduction
    _last_refund_time.clear()
    expect_success(
        "trigger_refund — valid refund succeeds and deducts balance",
        lambda: trigger_refund("CUST-00001", 50.00, "Item arrived broken and completely unusable"),
        check=lambda r: "PENDING" in r and "190.00" in r  # 250 - 10 (prior) - 50 = 190
    )

    # Duplicate protection
    expect_error("trigger_refund — blocks duplicate within 60s",
        lambda: trigger_refund("CUST-00001", 25.00, "Item arrived broken and completely unusable"),
        error_code="REFUND_003")

    # ── Metrics tool ─────────────────────────────────────────────────────────
    log("— Metrics Tool —")
    expect_success(
        "get_metrics — returns stats table with call counts",
        lambda: get_metrics(),
        check=lambda r: "get_customer_record" in r and "trigger_refund" in r
    )

    # ── Audit log verification ───────────────────────────────────────────────
    log("— Audit Log —")
    try:
        with open("audit.log", encoding="utf-8") as f:
            lines = [json.loads(l) for l in f if l.strip()]

        has_success = any(e["outcome"] == "success" for e in lines)
        has_error   = any(e["outcome"] in ("validation_error", "business_error") for e in lines)
        has_codes   = any(e["error_code"] for e in lines)
        has_ts      = all("timestamp" in e for e in lines)

        if has_success and has_error and has_codes and has_ts:
            results.append((PASS, f"audit.log — {len(lines)} entries written, all fields present"))
        else:
            results.append((FAIL, f"audit.log — missing fields: success={has_success} error={has_error} codes={has_codes} ts={has_ts}"))
    except Exception as e:
        results.append((FAIL, f"audit.log — could not read: {e}"))

    # ── Print results ────────────────────────────────────────────────────────
    log("")
    passed = sum(1 for r in results if r[0].startswith("✓"))
    for status, label in results:
        log(f"  {status}  {label}")

    log(f"\n  {passed}/{len(results)} tests passed")
    if passed == len(results):
        log("\n  ✅ All tests passed! Task 1 complete.\n")
    else:
        log("\n  ❌ Some tests failed — check errors above.\n")

    log("--- STDIO Isolation Check ---")
    log("Run: python server.py 2>$null")
    log("stdout must be completely silent.\n")

if __name__ == "__main__":
    asyncio.run(run_tests())