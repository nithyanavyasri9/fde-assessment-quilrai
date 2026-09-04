"""
Test suite for Task 3 — LLM Streaming PII Redaction Gateway.
Tests the redaction logic directly — no servers needed.

Run: python test_gateway.py
"""

import sys
import asyncio

def log(msg): print(msg, file=sys.stderr)


async def run_tests():
    from gateway import StreamingRedactor, extract_text_from_sse_line, build_sse_line

    PASS, FAIL = "✓ PASS", "✗ FAIL"
    results = []

    def expect(label, condition, detail=""):
        if condition:
            results.append((PASS, label))
        else:
            results.append((FAIL, f"{label}{' -- ' + detail if detail else ''}"))

    log("\n=== Task 3 PII Redaction Gateway Tests ===\n")

    # ── Single chunk redaction ────────────────────────────────────────────────
    log("-- Single Chunk Redaction --")

    def redact_full(text):
        r = StreamingRedactor()
        out = r.process_chunk(text)
        out += r.flush()
        return out

    expect("Email redacted",
        "[REDACTED-EMAIL]" in redact_full("Contact john.smith@example.com for help"))
    expect("SSN redacted",
        "[REDACTED-SSN]" in redact_full("SSN is 123-45-6789 on file"))
    expect("Credit card redacted",
        "[REDACTED-CC]" in redact_full("Card: 4111 1111 1111 1111 exp 12/26"))
    expect("Credit card no spaces redacted",
        "[REDACTED-CC]" in redact_full("Card: 4111111111111111"))
    expect("Phone (555) format redacted",
        "[REDACTED-PHONE]" in redact_full("Call us at (555) 123-4567 today"))
    expect("Phone dot format redacted",
        "[REDACTED-PHONE]" in redact_full("Call 555.123.4567 anytime"))
    expect("Multiple emails redacted",
        redact_full("a@b.com and c@d.com here").count("[REDACTED-EMAIL]") == 2)
    expect("Clean text passes through unchanged",
        redact_full("Hello, how can I help you today?") == "Hello, how can I help you today?")
    expect("Named tags used not generic [REDACTED]",
        "[REDACTED-EMAIL]" in redact_full("email: x@y.com") and
        "[REDACTED]" not in redact_full("email: x@y.com"))

    # ── Redaction counts ──────────────────────────────────────────────────────
    log("-- Redaction Audit Counts --")

    r = StreamingRedactor()
    r.process_chunk("Contact a@b.com and c@d.com now. ")
    r.process_chunk("SSN: 123-45-6789. Card: 4111 1111 1111 1111.")
    r.flush()
    expect("Counts 2 emails", r.redaction_counts["EMAIL"] == 2,
        f"got {r.redaction_counts['EMAIL']}")
    expect("Counts 1 SSN", r.redaction_counts["SSN"] == 1,
        f"got {r.redaction_counts['SSN']}")
    expect("Counts 1 credit card", r.redaction_counts["CREDIT_CARD"] == 1,
        f"got {r.redaction_counts['CREDIT_CARD']}")

    # ── CRITICAL: Cross-boundary PII detection ────────────────────────────────
    log("-- Cross-Chunk Boundary Detection (critical) --")

    # Email split: "john.sm" | "ith@example.com"
    r = StreamingRedactor()
    out = ""
    out += r.process_chunk("Email me at john.sm")
    out += r.process_chunk("ith@example.com tonight")
    out += r.flush()
    expect("Email spanning chunk boundary is redacted",
        "[REDACTED-EMAIL]" in out, f"got: {out!r}")
    expect("Email boundary -- no raw email in output",
        "john.smith@example.com" not in out)

    # SSN split: "123-45" | "-6789"
    r = StreamingRedactor()
    out = ""
    out += r.process_chunk("SSN: 123-45")
    out += r.process_chunk("-6789 on record")
    out += r.flush()
    expect("SSN spanning chunk boundary is redacted",
        "[REDACTED-SSN]" in out, f"got: {out!r}")
    expect("SSN boundary -- no raw SSN in output",
        "123-45-6789" not in out)

    # Credit card split: "4111 1111 " | "1111 1111"
    r = StreamingRedactor()
    out = ""
    out += r.process_chunk("Card: 4111 1111 ")
    out += r.process_chunk("1111 1111 expires")
    out += r.flush()
    expect("Credit card spanning chunk boundary is redacted",
        "[REDACTED-CC]" in out, f"got: {out!r}")
    expect("CC boundary -- no raw card number in output",
        "4111 1111 1111 1111" not in out)

    # ── Many small chunks (stress test) ──────────────────────────────────────
    log("-- Many Small Chunks (stress test) --")

    text = "Please email john.smith@example.com or call (555) 123-4567 for support."
    r = StreamingRedactor()
    out = ""
    # Feed one character at a time — worst case for boundary detection
    for char in text:
        out += r.process_chunk(char)
    out += r.flush()
    expect("Email detected feeding 1 char at a time",
        "[REDACTED-EMAIL]" in out, f"got: {out!r}")
    expect("Phone detected feeding 1 char at a time",
        "[REDACTED-PHONE]" in out, f"got: {out!r}")
    expect("Raw email not present in char-by-char output",
        "john.smith@example.com" not in out)

    # ── SSE parsing ───────────────────────────────────────────────────────────
    log("-- SSE Format Parsing --")

    import json
    line = 'data: {"choices":[{"delta":{"content":"Hello world"}}]}'
    expect("SSE line parsed correctly",
        extract_text_from_sse_line(line) == "Hello world")
    expect("SSE [DONE] returns None",
        extract_text_from_sse_line("data: [DONE]") is None)
    expect("Non-data line returns None",
        extract_text_from_sse_line(": keep-alive") is None)
    expect("Empty data line handled",
        extract_text_from_sse_line("data: ") is None)

    sse = build_sse_line("test content")
    parsed = json.loads(sse.replace("data: ", "").strip())
    expect("build_sse_line produces valid SSE",
        parsed["choices"][0]["delta"]["content"] == "test content")

    # ── Print results ─────────────────────────────────────────────────────────
    log("")
    passed = sum(1 for r in results if r[0].startswith("✓"))
    for status, label in results:
        log(f"  {status}  {label}")
    log(f"\n  {passed}/{len(results)} tests passed")
    if passed == len(results):
        log("\n  All tests passed! Task 3 complete.\n")
    else:
        log("\n  Some tests failed -- check above.\n")

    log("-- To test live streaming --")
    log("  Terminal 1: python mock_llm_server.py")
    log("  Terminal 2: python gateway.py")
    log("  Terminal 3: powershell -ExecutionPolicy Bypass -File .\\run_tests.ps1\n")


if __name__ == "__main__":
    asyncio.run(run_tests())
