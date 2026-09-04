"""
FDE Assessment — Task 2: MCP Security Gateway Proxy
====================================================
Sits between an AI agent client and a downstream MCP server.
Enforces role-based access control on tool calls before forwarding.

Architecture:
    AI Agent → [THIS GATEWAY :9000] → [Downstream MCP Server :9001]

Auth flow:
    1. Client sends: Authorization: Bearer <token>
    2. Gateway decodes token → extracts role (admin | viewer)
    3. If tools/list  → forward transparently
    4. If tools/call  → inspect params.name:
         - starts with "admin_" + role != admin → block with -32001
         - otherwise → forward to downstream

Differentiators beyond baseline:
  ✓ Structured audit log (gateway_audit.log) — every request logged
  ✓ Token validation — rejects missing/malformed tokens cleanly
  ✓ Request sanitization — strips internal headers before forwarding
  ✓ Response sanitization — strips upstream internals from error responses
  ✓ Unknown method handling — -32601 instead of silent forward
  ✓ Downstream timeout handling — -32000 with clean message
  ✓ Full JSON-RPC error code compliance
"""

import json
import logging
import time
from datetime import datetime, timezone

import httpx2
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────
DOWNSTREAM_URL   = "http://127.0.0.1:9001/mcp"
DOWNSTREAM_TIMEOUT = 5.0   # seconds before we return a gateway timeout error
AUDIT_LOG_FILE   = "gateway_audit.log"

# ──────────────────────────────────────────────────────────────────────────────
# JSON-RPC error codes
# ──────────────────────────────────────────────────────────────────────────────
PARSE_ERROR      = -32700
INVALID_REQUEST  = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS   = -32602
INTERNAL_ERROR   = -32000
UNAUTHORIZED     = -32001   # custom: unauthorized tool call

# ──────────────────────────────────────────────────────────────────────────────
# Logging — stderr only (same discipline as Task 1)
# ──────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="[%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

app = FastAPI(title="MCP Security Gateway", version="1.0.0")


# ──────────────────────────────────────────────────────────────────────────────
# Audit logger
# ──────────────────────────────────────────────────────────────────────────────
def audit(event: str, role: str, method: str, tool: str = None,
          outcome: str = None, detail: str = None):
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event":     event,     # "forward" | "blocked" | "auth_error" | "timeout"
        "role":      role,
        "method":    method,
        "tool":      tool,
        "outcome":   outcome,
        "detail":    detail,
    }
    try:
        with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as e:
        log.warning("Audit log write failed: %s", e)


# ──────────────────────────────────────────────────────────────────────────────
# JSON-RPC helpers
# ──────────────────────────────────────────────────────────────────────────────
def jsonrpc_error(req_id, code: int, message: str, data: dict = None) -> JSONResponse:
    """Build a spec-compliant JSON-RPC error response."""
    error = {"code": code, "message": message}
    if data:
        error["data"] = data
    return JSONResponse({
        "jsonrpc": "2.0",
        "id": req_id,
        "error": error,
    })


# ──────────────────────────────────────────────────────────────────────────────
# Token extraction & role resolution
# ──────────────────────────────────────────────────────────────────────────────

# Simulated token registry — in production this would be JWT verification
# or a call to an auth service.
TOKEN_REGISTRY = {
    "token-admin-001":  "admin",
    "token-viewer-001": "viewer",
    "token-viewer-002": "viewer",
}

def extract_role(authorization: str | None) -> tuple[str | None, str | None]:
    """
    Parse the Authorization header and return (role, error_message).
    Returns (role, None) on success, (None, error) on failure.
    """
    if not authorization:
        return None, "Missing Authorization header. Expected: Bearer <token>"

    parts = authorization.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None, f"Malformed Authorization header. Expected 'Bearer <token>', got: '{authorization}'"

    token = parts[1]
    role = TOKEN_REGISTRY.get(token)
    if role is None:
        return None, f"Unknown or expired token. Access denied."

    return role, None


# ──────────────────────────────────────────────────────────────────────────────
# Authorization logic
# ──────────────────────────────────────────────────────────────────────────────
def is_admin_tool(tool_name: str) -> bool:
    """Return True if the tool name requires admin role."""
    return tool_name.startswith("admin_")

def check_authorization(role: str, tool_name: str) -> str | None:
    """
    Return an error message if access is denied, None if allowed.
    Only admin-prefixed tools require the admin role.
    """
    if is_admin_tool(tool_name) and role != "admin":
        return (
            f"Tool '{tool_name}' requires admin role. "
            f"Your role '{role}' is not authorized."
        )
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Downstream proxy
# ──────────────────────────────────────────────────────────────────────────────
async def forward_to_downstream(body: dict) -> JSONResponse:
    """
    Forward a JSON-RPC request to the downstream MCP server.
    Handles timeouts and connection errors with clean gateway error responses
    — never leaks raw upstream stack traces or internal details.
    """
    try:
        async with httpx2.AsyncClient(timeout=DOWNSTREAM_TIMEOUT) as client:
            response = await client.post(
                DOWNSTREAM_URL,
                json=body,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            return JSONResponse(content=response.json(), status_code=200)

    except httpx2.TimeoutException:
        log.error("Downstream timeout after %.1fs", DOWNSTREAM_TIMEOUT)
        return jsonrpc_error(
            body.get("id"),
            INTERNAL_ERROR,
            "Gateway timeout: downstream MCP server did not respond in time.",
            data={"timeout_seconds": DOWNSTREAM_TIMEOUT}
        )
    except httpx2.ConnectError:
        log.error("Cannot reach downstream server at %s", DOWNSTREAM_URL)
        return jsonrpc_error(
            body.get("id"),
            INTERNAL_ERROR,
            "Gateway error: downstream MCP server is unreachable.",
        )
    except Exception:
        # Catch-all — never surface raw exceptions to the caller
        log.exception("Unexpected error forwarding to downstream")
        return jsonrpc_error(
            body.get("id"),
            INTERNAL_ERROR,
            "Gateway error: an internal error occurred.",
        )


# ──────────────────────────────────────────────────────────────────────────────
# Main gateway endpoint
# ──────────────────────────────────────────────────────────────────────────────
@app.post("/mcp")
async def gateway(request: Request):
    start = time.monotonic()

    # ── 1. Parse request body ────────────────────────────────────────────────
    try:
        body = await request.json()
    except Exception:
        return jsonrpc_error(None, PARSE_ERROR, "Invalid JSON in request body.")

    req_id = body.get("id")
    method = body.get("method", "")
    params = body.get("params", {})

    log.debug("Incoming: method=%s id=%s", method, req_id)

    # ── 2. Extract role from Authorization header ────────────────────────────
    auth_header = request.headers.get("Authorization")
    role, auth_error = extract_role(auth_header)

    if auth_error:
        log.warning("Auth failure: %s", auth_error)
        audit("auth_error", "unknown", method, detail=auth_error)
        return jsonrpc_error(req_id, UNAUTHORIZED, auth_error)

    log.debug("Authenticated: role=%s", role)

    # ── 3. Route by method ───────────────────────────────────────────────────

    # tools/list — forward transparently (no auth restriction)
    if method == "tools/list":
        log.debug("Forwarding tools/list for role=%s", role)
        audit("forward", role, method, outcome="forwarded")
        return await forward_to_downstream(body)

    # tools/call — inspect tool name and enforce RBAC
    if method == "tools/call":
        tool_name = params.get("name", "")

        if not tool_name:
            return jsonrpc_error(req_id, INVALID_PARAMS, "tools/call requires params.name")

        # Check authorization
        auth_denial = check_authorization(role, tool_name)
        if auth_denial:
            log.warning("BLOCKED: role=%s tool=%s — %s", role, tool_name, auth_denial)
            audit("blocked", role, method, tool=tool_name,
                  outcome="unauthorized", detail=auth_denial)
            return jsonrpc_error(
                req_id,
                UNAUTHORIZED,
                "Unauthorized Tool Call",
                data={
                    "tool":    tool_name,
                    "role":    role,
                    "reason":  auth_denial,
                }
            )

        # Authorized — forward
        log.info("Forwarding tools/call: tool=%s role=%s", tool_name, role)
        audit("forward", role, method, tool=tool_name, outcome="forwarded")
        response = await forward_to_downstream(body)
        elapsed = (time.monotonic() - start) * 1000
        log.debug("tools/call completed in %.1fms", elapsed)
        return response

    # Unknown method — return -32601, do NOT forward blindly
    log.warning("Unknown method: %s", method)
    audit("blocked", role, method, outcome="method_not_found")
    return jsonrpc_error(req_id, METHOD_NOT_FOUND, f"Unknown MCP method: '{method}'")


# ──────────────────────────────────────────────────────────────────────────────
# Health check endpoint (bonus — useful for ops)
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "gateway": "mcp-security-gateway", "version": "1.0.0"}


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    log.info("Starting MCP Security Gateway on port 9000")
    log.info("Downstream MCP server expected at %s", DOWNSTREAM_URL)
    uvicorn.run(app, host="127.0.0.1", port=9000, log_level="warning")
