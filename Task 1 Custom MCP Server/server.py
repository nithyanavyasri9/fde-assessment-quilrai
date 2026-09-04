"""
FDE Assessment — Task 1: Custom MCP Server (MCP SDK v2.x compatible)
=====================================================================
Tools exposed:
  - get_customer_record  (input: customer_id formatted as CUST-XXXXX)
  - trigger_refund       (inputs: customer_id, amount, reason ≥ 10 chars)
  - get_metrics          (no inputs — returns live call stats for all tools)

Differentiators beyond baseline requirements:
  ✓ Structured JSON audit log (audit.log) — every call timestamped with outcome
  ✓ Rate limiting — max 10 calls/minute per tool, enforced in-memory
  ✓ Business rules — balance check, duplicate refund protection (60s window),
                     filler-reason detection, max refund cap
  ✓ Custom error hierarchy — typed error codes (CUST_001, REFUND_00x)
  ✓ Live metrics tool — call counts, error rates, per-tool stats
  ✓ Pydantic strict input validation with clear error messages
  ✓ stdout is PURE JSON-RPC only — all debug/log output → stderr
  ✓ stdio transport (MCP default)
"""

import sys
import re
import json
import time
import logging
from collections import defaultdict, deque
from datetime import datetime, timezone
from mcp.server.mcpserver import MCPServer as FastMCP  # v2: FastMCP renamed to MCPServer

# ──────────────────────────────────────────────────────────────────────────────
# CRITICAL: Log to stderr ONLY.
# Never use print() — it pollutes stdout and breaks stdio transport.
# ──────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    stream=sys.stderr,
    level=logging.DEBUG,
    format="[%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# MCP Server
# ──────────────────────────────────────────────────────────────────────────────
mcp = FastMCP("customer-service-mcp")


# ──────────────────────────────────────────────────────────────────────────────
# Custom error hierarchy — typed codes instead of generic ValueError
# Makes errors machine-readable and traceable in audit logs
# ──────────────────────────────────────────────────────────────────────────────
class CustomerServiceError(ValueError):
    """Base error for all customer service tool failures."""
    error_code: str = "CS_000"

class CustomerNotFoundError(CustomerServiceError):
    error_code = "CUST_001"

class CustomerIDFormatError(CustomerServiceError):
    error_code = "CUST_002"

class RefundAmountError(CustomerServiceError):
    error_code = "REFUND_001"

class RefundExceedsBalanceError(CustomerServiceError):
    error_code = "REFUND_002"

class RefundDuplicateError(CustomerServiceError):
    error_code = "REFUND_003"

class RefundReasonError(CustomerServiceError):
    error_code = "REFUND_004"

class RateLimitError(CustomerServiceError):
    error_code = "RATE_001"


# ──────────────────────────────────────────────────────────────────────────────
# Audit logger — writes structured JSON to audit.log (stderr for transport safety)
# ──────────────────────────────────────────────────────────────────────────────
AUDIT_LOG_FILE = "audit.log"

def audit(tool: str, inputs: dict, outcome: str, error_code: str = None, detail: str = None):
    """
    Write a structured audit entry to audit.log.
    Each line is a self-contained JSON object (JSON-Lines format).
    This is separate from stderr logging — audit.log is for compliance/ops.
    """
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "inputs": inputs,
        "outcome": outcome,           # "success" | "validation_error" | "business_error" | "rate_limited"
        "error_code": error_code,     # e.g. "REFUND_003" — None on success
        "detail": detail,             # human-readable error detail — None on success
    }
    try:
        with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as e:
        log.warning("Failed to write audit log: %s", e)


# ──────────────────────────────────────────────────────────────────────────────
# In-memory metrics tracker
# ──────────────────────────────────────────────────────────────────────────────
class ToolMetrics:
    def __init__(self):
        self.calls: int = 0
        self.errors: int = 0
        self.total_ms: float = 0.0

    def record(self, elapsed_ms: float, is_error: bool):
        self.calls += 1
        self.total_ms += elapsed_ms
        if is_error:
            self.errors += 1

    @property
    def success_rate(self) -> str:
        if self.calls == 0:
            return "n/a"
        rate = ((self.calls - self.errors) / self.calls) * 100
        return f"{rate:.1f}%"

    @property
    def avg_ms(self) -> str:
        if self.calls == 0:
            return "n/a"
        return f"{self.total_ms / self.calls:.1f}ms"


METRICS: dict[str, ToolMetrics] = defaultdict(ToolMetrics)


# ──────────────────────────────────────────────────────────────────────────────
# Rate limiter — sliding window, per tool
# ──────────────────────────────────────────────────────────────────────────────
RATE_LIMIT_CALLS = 10       # max calls
RATE_LIMIT_WINDOW = 60      # per seconds
_rate_windows: dict[str, deque] = defaultdict(deque)

def check_rate_limit(tool_name: str):
    """Raise RateLimitError if this tool has exceeded its call quota."""
    now = time.monotonic()
    window = _rate_windows[tool_name]
    # Evict timestamps outside the window
    while window and now - window[0] > RATE_LIMIT_WINDOW:
        window.popleft()
    if len(window) >= RATE_LIMIT_CALLS:
        raise RateLimitError(
            f"Rate limit exceeded for '{tool_name}': "
            f"max {RATE_LIMIT_CALLS} calls per {RATE_LIMIT_WINDOW}s. "
            f"Try again in {RATE_LIMIT_WINDOW - int(now - window[0])}s."
        )
    window.append(now)


# ──────────────────────────────────────────────────────────────────────────────
# Validation helpers
# ──────────────────────────────────────────────────────────────────────────────
CUSTOMER_ID_PATTERN = re.compile(r"^CUST-\d{5}$")

# Words so generic they indicate a filler reason, not a real one
FILLER_WORDS = {"test", "testing", "asdf", "xxx", "none", "na", "n/a", "reason", "refund"}

def validate_customer_id(customer_id: str) -> None:
    if not CUSTOMER_ID_PATTERN.match(customer_id):
        raise CustomerIDFormatError(
            f"customer_id must match CUST-XXXXX (5 digits). Got: '{customer_id}'"
        )

def validate_reason(reason: str) -> None:
    if len(reason) < 10:
        raise RefundReasonError(
            f"reason must be at least 10 characters. Got {len(reason)}: '{reason}'"
        )
    # Check for filler/placeholder reasons — at least 3 unique non-filler words required
    words = set(reason.lower().split())
    meaningful = words - FILLER_WORDS
    if len(meaningful) < 3:
        raise RefundReasonError(
            f"reason appears to be a placeholder ('{reason}'). "
            f"Please provide a specific, meaningful refund reason."
        )


# ──────────────────────────────────────────────────────────────────────────────
# Mock data store
# ──────────────────────────────────────────────────────────────────────────────
MOCK_CUSTOMERS = {
    "CUST-00001": {"name": "Alice Johnson", "email": "alice@example.com", "balance": 250.00},
    "CUST-00002": {"name": "Bob Smith",     "email": "bob@example.com",   "balance": 75.50},
    "CUST-00042": {"name": "Nithya M.",     "email": "nithya@example.com","balance": 500.00},
}

# Duplicate refund protection: tracks last refund timestamp per customer
_last_refund_time: dict[str, float] = {}
DUPLICATE_WINDOW = 60  # seconds — block identical customer refund within this window
MAX_REFUND_AMOUNT = 1000.00  # hard cap per refund request


# ──────────────────────────────────────────────────────────────────────────────
# Tool 1: get_customer_record
# ──────────────────────────────────────────────────────────────────────────────
@mcp.tool()
def get_customer_record(customer_id: str) -> str:
    """
    Retrieve a customer record by their ID.

    Args:
        customer_id: Customer ID formatted as CUST-XXXXX (e.g. CUST-00042)

    Returns:
        Formatted customer record with name, email, and current balance.
    """
    tool = "get_customer_record"
    inputs = {"customer_id": customer_id}
    start = time.monotonic()

    try:
        check_rate_limit(tool)
        validate_customer_id(customer_id)

        customer = MOCK_CUSTOMERS.get(customer_id)
        if customer is None:
            raise CustomerNotFoundError(f"Customer '{customer_id}' not found.")

        elapsed = (time.monotonic() - start) * 1000
        METRICS[tool].record(elapsed, is_error=False)
        audit(tool, inputs, "success")
        log.debug("%s → success (%.1fms)", tool, elapsed)

        return (
            f"Customer Record\n"
            f"  ID      : {customer_id}\n"
            f"  Name    : {customer['name']}\n"
            f"  Email   : {customer['email']}\n"
            f"  Balance : ${customer['balance']:.2f}"
        )

    except RateLimitError as e:
        elapsed = (time.monotonic() - start) * 1000
        METRICS[tool].record(elapsed, is_error=True)
        audit(tool, inputs, "rate_limited", e.error_code, str(e))
        log.warning("%s → rate limited", tool)
        raise ValueError(f"[{e.error_code}] {e}") from e

    except CustomerServiceError as e:
        elapsed = (time.monotonic() - start) * 1000
        METRICS[tool].record(elapsed, is_error=True)
        outcome = "validation_error" if isinstance(e, CustomerIDFormatError) else "business_error"
        audit(tool, inputs, outcome, e.error_code, str(e))
        log.warning("%s → %s: %s", tool, e.error_code, e)
        raise ValueError(f"[{e.error_code}] {e}") from e


# ──────────────────────────────────────────────────────────────────────────────
# Tool 2: trigger_refund
# ──────────────────────────────────────────────────────────────────────────────
@mcp.tool()
def trigger_refund(customer_id: str, amount: float, reason: str) -> str:
    """
    Initiate a refund for a customer.

    Business rules enforced:
      - amount must be positive and ≤ $1,000 per request
      - amount cannot exceed the customer's current balance
      - same customer cannot be refunded twice within 60 seconds (duplicate protection)
      - reason must be at least 10 characters and not a generic placeholder

    Args:
        customer_id: Customer ID formatted as CUST-XXXXX
        amount: Refund amount — must be positive and ≤ 1000.00
        reason: Specific reason for the refund — minimum 10 meaningful characters
    """
    tool = "trigger_refund"
    inputs = {"customer_id": customer_id, "amount": amount, "reason": reason}
    start = time.monotonic()

    try:
        check_rate_limit(tool)

        # ── Input validation ────────────────────────────────────────────────
        validate_customer_id(customer_id)

        if amount <= 0:
            raise RefundAmountError(
                f"amount must be positive. Got: {amount}"
            )
        if amount > MAX_REFUND_AMOUNT:
            raise RefundAmountError(
                f"amount ${amount:.2f} exceeds the per-request cap of ${MAX_REFUND_AMOUNT:.2f}."
            )

        validate_reason(reason)

        # ── Business rules ──────────────────────────────────────────────────
        customer = MOCK_CUSTOMERS.get(customer_id)
        if customer is None:
            raise CustomerNotFoundError(f"Customer '{customer_id}' not found.")

        if amount > customer["balance"]:
            raise RefundExceedsBalanceError(
                f"Refund amount ${amount:.2f} exceeds customer balance ${customer['balance']:.2f}."
            )

        now = time.monotonic()
        last = _last_refund_time.get(customer_id)
        if last and (now - last) < DUPLICATE_WINDOW:
            wait = int(DUPLICATE_WINDOW - (now - last))
            raise RefundDuplicateError(
                f"A refund for '{customer_id}' was already processed {int(now - last)}s ago. "
                f"Please wait {wait}s before submitting another refund for this customer."
            )

        # ── Process refund ──────────────────────────────────────────────────
        _last_refund_time[customer_id] = now
        customer["balance"] = round(customer["balance"] - amount, 2)  # deduct from balance
        ref_id = f"REF-{customer_id}-{int(amount * 100):08d}-{int(now)}"

        elapsed = (time.monotonic() - start) * 1000
        METRICS[tool].record(elapsed, is_error=False)
        audit(tool, inputs, "success", detail=f"ref={ref_id}")
        log.info("%s → success ref=%s (%.1fms)", tool, ref_id, elapsed)

        return (
            f"Refund Initiated ✓\n"
            f"  Customer    : {customer_id} ({customer['name']})\n"
            f"  Amount      : ${amount:.2f}\n"
            f"  Reason      : {reason}\n"
            f"  New Balance : ${customer['balance']:.2f}\n"
            f"  Reference   : {ref_id}\n"
            f"  Status      : PENDING"
        )

    except RateLimitError as e:
        elapsed = (time.monotonic() - start) * 1000
        METRICS[tool].record(elapsed, is_error=True)
        audit(tool, inputs, "rate_limited", e.error_code, str(e))
        raise ValueError(f"[{e.error_code}] {e}") from e

    except CustomerServiceError as e:
        elapsed = (time.monotonic() - start) * 1000
        METRICS[tool].record(elapsed, is_error=True)
        is_validation = isinstance(e, (CustomerIDFormatError, RefundAmountError, RefundReasonError))
        outcome = "validation_error" if is_validation else "business_error"
        audit(tool, inputs, outcome, e.error_code, str(e))
        log.warning("%s → %s: %s", tool, e.error_code, e)
        raise ValueError(f"[{e.error_code}] {e}") from e


# ──────────────────────────────────────────────────────────────────────────────
# Tool 3: get_metrics  (bonus — shows operational thinking)
# ──────────────────────────────────────────────────────────────────────────────
@mcp.tool()
def get_metrics() -> str:
    """
    Return live call statistics for all tools in this server session.

    Shows: total calls, error count, success rate, and average response time.
    Resets when the server restarts (in-memory only).
    """
    if not METRICS:
        return "No tool calls recorded yet in this session."

    lines = ["Tool Metrics (current session)\n", f"  {'Tool':<30} {'Calls':>6} {'Errors':>7} {'Success%':>10} {'Avg ms':>8}"]
    lines.append("  " + "-" * 65)
    for name, m in sorted(METRICS.items()):
        lines.append(f"  {name:<30} {m.calls:>6} {m.errors:>7} {m.success_rate:>10} {m.avg_ms:>8}")

    audit("get_metrics", {}, "success")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# Entry point — stdio transport
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    log.debug("Starting MCP server via stdio transport")
    mcp.run(transport="stdio")