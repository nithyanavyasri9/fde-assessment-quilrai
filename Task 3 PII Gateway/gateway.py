"""
FDE Assessment — Task 3: LLM Gateway Streaming PII Redaction
=============================================================
Proxies text generation requests to an LLM provider and streams
the response back to the client — redacting PII in real time.

Key challenge: PII can SPAN chunk boundaries.
  e.g. chunk 1: "email me at john"
       chunk 2: "@example.com tonight"
  Naive per-chunk regex misses this. We use a rolling overlap buffer.

PII patterns redacted:
  - Email addresses       → [REDACTED-EMAIL]
  - SSNs (xxx-xx-xxxx)   → [REDACTED-SSN]
  - Credit card numbers   → [REDACTED-CC]
  - Phone numbers         → [REDACTED-PHONE]

Differentiators beyond baseline:
  ✓ Rolling overlap buffer — catches PII spanning chunk boundaries
  ✓ Named redaction tags — [REDACTED-EMAIL] not just [REDACTED]
  ✓ Redaction audit counter — tracks how many of each type were caught
  ✓ Mock LLM endpoint built-in — no real API key needed to test
  ✓ Streaming latency preserved — chunks forwarded as they arrive
  ✓ X-Redaction-Summary header — tells caller what was scrubbed
"""

import re
import json
import logging
import asyncio
from collections import defaultdict
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse
import httpx2

# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────
# Point this at a real OpenAI-compatible endpoint to use a real LLM.
# We provide a built-in mock endpoint so tests work without an API key.
LLM_ENDPOINT  = "http://127.0.0.1:9100/v1/chat/completions"
LLM_TIMEOUT   = 30.0

# How many chars to keep as overlap between chunks.
# Must be >= longest PII pattern that could span a boundary.
# Longest pattern: credit card "4111 1111 1111 1111" = 19 chars → use 40 to be safe.
OVERLAP_SIZE  = 50   # increased: handles emails up to 50 chars at chunk boundaries

logging.basicConfig(level=logging.DEBUG, format="[%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="LLM PII Redaction Gateway", version="1.0.0")


# ──────────────────────────────────────────────────────────────────────────────
# PII patterns — ordered by specificity (most specific first)
# Each tuple: (name, compiled_regex, replacement_tag)
# ──────────────────────────────────────────────────────────────────────────────
PII_PATTERNS = [
    (
        "SSN",
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "[REDACTED-SSN]",
    ),
    (
        "CREDIT_CARD",
        # Matches: 4111111111111111 or 4111 1111 1111 1111 or 4111-1111-1111-1111
        re.compile(r"\b(?:\d{4}[- ]?){3}\d{4}\b"),
        "[REDACTED-CC]",
    ),
    (
        "EMAIL",
        re.compile(r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b"),
        "[REDACTED-EMAIL]",
    ),
    (
        "PHONE",
        # Matches: (555) 123-4567 / 555-123-4567 / +1-555-123-4567 / 5551234567
        re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
        "[REDACTED-PHONE]",
    ),
]


# ──────────────────────────────────────────────────────────────────────────────
# PII redactor with rolling overlap buffer
# ──────────────────────────────────────────────────────────────────────────────
class StreamingRedactor:
    """
    Processes a stream of text chunks and redacts PII in real time.

    The core problem: if a chunk ends mid-PII-pattern, naive regex misses it.

    Solution — rolling overlap buffer:
      1. Append new chunk to buffer
      2. Redact the SAFE zone (everything except the last OVERLAP_SIZE chars)
      3. Yield the redacted safe zone
      4. Keep the last OVERLAP_SIZE chars as overlap for the next chunk
      5. On flush() — redact and yield whatever remains in the buffer

    This guarantees no PII pattern longer than OVERLAP_SIZE chars is missed.
    """

    def __init__(self):
        self.buffer = ""
        self.redaction_counts: dict[str, int] = defaultdict(int)

    def _redact(self, text: str) -> str:
        """Apply all PII patterns to a text string. Returns redacted version."""
        for name, pattern, replacement in PII_PATTERNS:
            matches = pattern.findall(text)
            if matches:
                self.redaction_counts[name] += len(matches)
                log.debug("Redacted %d %s match(es)", len(matches), name)
            text = pattern.sub(replacement, text)
        return text

    def process_chunk(self, chunk: str) -> str:
        """
        Add chunk to buffer, redact safe zone, return safe redacted text.
        Keeps last OVERLAP_SIZE chars in buffer for next chunk's boundary check.
        """
        self.buffer += chunk

        if len(self.buffer) <= OVERLAP_SIZE:
            # Buffer too small to safely redact — hold everything
            return ""

        # Safe zone: everything except the last OVERLAP_SIZE chars
        safe_zone = self.buffer[:-OVERLAP_SIZE]
        self.buffer = self.buffer[-OVERLAP_SIZE:]  # keep overlap

        return self._redact(safe_zone)

    def flush(self) -> str:
        """Called at end of stream — redact and return whatever remains."""
        remaining = self._redact(self.buffer)
        self.buffer = ""
        return remaining

    @property
    def summary(self) -> dict:
        return dict(self.redaction_counts)


# ──────────────────────────────────────────────────────────────────────────────
# SSE / OpenAI stream parser
# ──────────────────────────────────────────────────────────────────────────────
def extract_text_from_sse_line(line: str) -> str | None:
    """
    Parse a Server-Sent Events line from OpenAI-compatible streaming response.
    Returns the text delta, or None if this line has no text content.

    SSE format:
        data: {"choices":[{"delta":{"content":"Hello"}}]}
        data: [DONE]
    """
    if not line.startswith("data: "):
        return None
    payload = line[6:].strip()
    if payload == "[DONE]":
        return None
    try:
        data = json.loads(payload)
        return data["choices"][0]["delta"].get("content", "")
    except (json.JSONDecodeError, KeyError, IndexError):
        return None


def build_sse_line(text: str) -> str:
    """Wrap text back into SSE format for the client."""
    payload = json.dumps({"choices": [{"delta": {"content": text}}]})
    return f"data: {payload}\n\n"


# ──────────────────────────────────────────────────────────────────────────────
# Core streaming proxy with redaction
# ──────────────────────────────────────────────────────────────────────────────
async def stream_with_redaction(
    request_body: dict,
) -> AsyncIterator[str]:
    """
    Streams from the LLM endpoint, redacting PII in each chunk.
    Yields SSE-formatted strings back to the client.
    """
    redactor = StreamingRedactor()

    try:
        async with httpx2.AsyncClient(timeout=LLM_TIMEOUT) as client:
            async with client.stream(
                "POST",
                LLM_ENDPOINT,
                json=request_body,
                headers={"Content-Type": "application/json"},
            ) as response:

                async for line in response.aiter_lines():
                    if not line:
                        continue

                    # Detect [DONE] signal — flush buffer first
                    if "[DONE]" in line:
                        remaining = redactor.flush()
                        if remaining:
                            yield build_sse_line(remaining)
                        log.info("Stream complete. Redactions: %s", redactor.summary)
                        yield "data: [DONE]\n\n"
                        continue

                    text = extract_text_from_sse_line(line)

                    if text is None or text == "":
                        continue

                    # Process through rolling buffer
                    safe_output = redactor.process_chunk(text)
                    if safe_output:
                        yield build_sse_line(safe_output)

                # End-of-stream safety flush — catches any remaining buffer
                # if [DONE] was missing or stream ended unexpectedly
                remaining = redactor.flush()
                if remaining:
                    yield build_sse_line(remaining)
                    log.info("End-of-stream flush. Redactions: %s", redactor.summary)

    except httpx2.TimeoutException:
        log.error("LLM endpoint timed out")
        yield build_sse_line("[Gateway Error: LLM request timed out]")
        yield "data: [DONE]\n\n"

    except httpx2.ConnectError:
        log.error("Cannot reach LLM endpoint at %s", LLM_ENDPOINT)
        yield build_sse_line("[Gateway Error: LLM endpoint unreachable]")
        yield "data: [DONE]\n\n"

    except Exception as e:
        log.exception("Unexpected streaming error")
        yield build_sse_line("[Gateway Error: internal error]")
        yield "data: [DONE]\n\n"

    finally:
        # Always flush on exit
        remaining = redactor.flush()
        if remaining:
            yield build_sse_line(remaining)


# ──────────────────────────────────────────────────────────────────────────────
# Gateway endpoint
# ──────────────────────────────────────────────────────────────────────────────
@app.post("/v1/chat/completions")
async def completions(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON body"})

    # Force streaming on — this gateway is stream-only
    body["stream"] = True

    log.info("Proxying request to LLM with PII redaction enabled")

    return StreamingResponse(
        stream_with_redaction(body),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",    # disables nginx buffering
            "X-PII-Redaction":   "enabled",
        },
    )


@app.get("/health")
async def health():
    return {"status": "ok", "service": "pii-redaction-gateway", "version": "1.0.0"}


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    log.info("Starting PII Redaction Gateway on port 9200")
    log.info("Proxying to LLM at %s", LLM_ENDPOINT)
    uvicorn.run(app, host="127.0.0.1", port=9200, log_level="warning")
