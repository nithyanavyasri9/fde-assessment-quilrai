"""
Test suite for Task 2 — MCP Security Gateway Proxy.

Tests all scenarios WITHOUT needing the servers running:
  - directly calls gateway logic functions
  - mocks the downstream HTTP call

Run: python test_gateway.py
"""

import sys
import asyncio
import json
import os
from unittest.mock import AsyncMock, patch, MagicMock

def log(msg): print(msg, file=sys.stderr)


async def run_tests():
    if os.path.exists("gateway_audit.log"):
        os.remove("gateway_audit.log")

    from gateway import (
        extract_role, check_authorization, is_admin_tool,
        jsonrpc_error, gateway, TOKEN_REGISTRY
    )

    PASS, FAIL = "✓ PASS", "✗ FAIL"
    results = []

    def expect(label, condition, detail=""):
        if condition:
            results.append((PASS, label))
        else:
            results.append((FAIL, f"{label}{' — ' + detail if detail else ''}"))

    def expect_error(label, fn, keyword=None):
        try:
            fn()
            results.append((FAIL, f"{label} — should have raised"))
        except Exception as e:
            if keyword and keyword.lower() not in str(e).lower():
                results.append((FAIL, f"{label} — wrong error: {e}"))
            else:
                results.append((PASS, label))

    log("\n=== Task 2 MCP Security Gateway Tests ===\n")

    # ── Token / Role extraction ───────────────────────────────────────────────
    log("— Token & Role Extraction —")

    role, err = extract_role("Bearer token-admin-001")
    expect("admin token resolves to role=admin", role == "admin" and err is None)

    role, err = extract_role("Bearer token-viewer-001")
    expect("viewer token resolves to role=viewer", role == "viewer" and err is None)

    role, err = extract_role(None)
    expect("missing Authorization header returns error", role is None and err is not None)

    role, err = extract_role("token-admin-001")  # missing "Bearer "
    expect("malformed header (no Bearer prefix) returns error", role is None and err is not None)

    role, err = extract_role("Bearer invalid-token-xyz")
    expect("unknown token returns error", role is None and "denied" in err.lower())

    role, err = extract_role("Basic dXNlcjpwYXNz")  # Basic auth, wrong scheme
    expect("wrong auth scheme (Basic) returns error", role is None and err is not None)

    # ── Authorization logic ───────────────────────────────────────────────────
    log("— Authorization Logic —")

    expect("admin_ prefix detected correctly",
        is_admin_tool("admin_reset_key") and is_admin_tool("admin_delete_account"))
    expect("non-admin tools not flagged",
        not is_admin_tool("get_customer_record") and not is_admin_tool("trigger_refund"))
    expect("admin role can call admin_reset_key",
        check_authorization("admin", "admin_reset_key") is None)
    expect("admin role can call regular tools",
        check_authorization("admin", "get_customer_record") is None)
    expect("viewer role BLOCKED from admin_reset_key",
        check_authorization("viewer", "admin_reset_key") is not None)
    expect("viewer role can call regular tools",
        check_authorization("viewer", "get_customer_record") is None)

    # ── JSON-RPC error format ─────────────────────────────────────────────────
    log("— JSON-RPC Error Format —")

    err_resp = jsonrpc_error(42, -32001, "Unauthorized Tool Call", {"tool": "admin_reset_key"})
    body = json.loads(err_resp.body)
    expect("error response has jsonrpc=2.0", body.get("jsonrpc") == "2.0")
    expect("error response has correct id", body.get("id") == 42)
    expect("error response has error.code=-32001", body["error"]["code"] == -32001)
    expect("error response has error.message", "Unauthorized" in body["error"]["message"])
    expect("error response has error.data.tool", body["error"].get("data", {}).get("tool") == "admin_reset_key")
    expect("error response has NO 'result' key", "result" not in body)

    # ── Full gateway request simulation ──────────────────────────────────────
    log("— Gateway Request Simulation —")

    # Build a fake Request object
    def make_request(method, params=None, token="token-viewer-001"):
        body = {"jsonrpc": "2.0", "id": 1, "method": method}
        if params:
            body["params"] = params

        req = MagicMock()
        req.headers = {"Authorization": f"Bearer {token}"}
        req.json = AsyncMock(return_value=body)
        return req

    downstream_ok = {
        "jsonrpc": "2.0", "id": 1,
        "result": {"content": [{"type": "text", "text": "ok"}]}
    }

    # tools/list always forwards
    with patch("gateway.forward_to_downstream", new_callable=AsyncMock) as mock_fwd:
        mock_fwd.return_value = MagicMock(body=json.dumps(downstream_ok).encode())
        req = make_request("tools/list", token="token-viewer-001")
        await gateway(req)
        expect("tools/list is forwarded for viewer", mock_fwd.called)

    # viewer calling regular tool — should forward
    with patch("gateway.forward_to_downstream", new_callable=AsyncMock) as mock_fwd:
        mock_fwd.return_value = MagicMock(body=json.dumps(downstream_ok).encode())
        req = make_request("tools/call", {"name": "get_customer_record"}, token="token-viewer-001")
        resp = await gateway(req)
        expect("viewer calling regular tool is forwarded", mock_fwd.called)

    # viewer calling admin_ tool — should be BLOCKED
    with patch("gateway.forward_to_downstream", new_callable=AsyncMock) as mock_fwd:
        req = make_request("tools/call", {"name": "admin_reset_key"}, token="token-viewer-001")
        resp = await gateway(req)
        body = json.loads(resp.body)
        expect("viewer calling admin_reset_key is BLOCKED (not forwarded)", not mock_fwd.called)
        expect("blocked response has code=-32001", body["error"]["code"] == -32001)
        expect("blocked response message is 'Unauthorized Tool Call'",
            body["error"]["message"] == "Unauthorized Tool Call")
        expect("blocked response data contains tool name",
            body["error"]["data"]["tool"] == "admin_reset_key")

    # admin calling admin_ tool — should forward
    with patch("gateway.forward_to_downstream", new_callable=AsyncMock) as mock_fwd:
        mock_fwd.return_value = MagicMock(body=json.dumps(downstream_ok).encode())
        req = make_request("tools/call", {"name": "admin_reset_key"}, token="token-admin-001")
        resp = await gateway(req)
        expect("admin calling admin_reset_key is forwarded", mock_fwd.called)

    # no auth header
    with patch("gateway.forward_to_downstream", new_callable=AsyncMock) as mock_fwd:
        req = MagicMock()
        req.headers = {}
        req.json = AsyncMock(return_value={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        resp = await gateway(req)
        body = json.loads(resp.body)
        expect("request with no token is rejected", body.get("error") is not None)
        expect("no-token rejection is -32001", body["error"]["code"] == -32001)
        expect("downstream NOT called when no token", not mock_fwd.called)

    # unknown method
    with patch("gateway.forward_to_downstream", new_callable=AsyncMock) as mock_fwd:
        req = make_request("some/unknown/method", token="token-admin-001")
        resp = await gateway(req)
        body = json.loads(resp.body)
        expect("unknown method returns -32601", body["error"]["code"] == -32601)
        expect("unknown method NOT forwarded", not mock_fwd.called)

    # ── Audit log ─────────────────────────────────────────────────────────────
    log("— Audit Log —")
    try:
        with open("gateway_audit.log", encoding="utf-8") as f:
            entries = [json.loads(l) for l in f if l.strip()]
        has_blocked  = any(e["event"] == "blocked" for e in entries)
        has_forward  = any(e["event"] == "forward" for e in entries)
        has_auth_err = any(e["event"] == "auth_error" for e in entries)
        has_ts       = all("timestamp" in e for e in entries)
        expect(f"gateway_audit.log — {len(entries)} entries, all events present",
            has_blocked and has_forward and has_auth_err and has_ts)
    except Exception as e:
        results.append((FAIL, f"gateway_audit.log — {e}"))

    # ── Print results ─────────────────────────────────────────────────────────
    log("")
    passed = sum(1 for r in results if r[0].startswith("✓"))
    for status, label in results:
        log(f"  {status}  {label}")
    log(f"\n  {passed}/{len(results)} tests passed")
    if passed == len(results):
        log("\n  ✅ All tests passed! Task 2 complete.\n")
    else:
        log("\n  ❌ Some tests failed — check above.\n")

if __name__ == "__main__":
    asyncio.run(run_tests())
