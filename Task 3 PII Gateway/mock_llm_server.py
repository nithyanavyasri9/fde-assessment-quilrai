"""
Mock LLM server — simulates an OpenAI-compatible streaming endpoint.
Streams a response containing various PII types to test redaction.
Runs on port 9100.

Start: python mock_llm_server.py
"""

import json
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
import uvicorn

app = FastAPI(title="Mock LLM Server")

# Test response containing all PII types, some spanning chunk boundaries
# We split deliberately to test boundary crossing
# Chunks are sized so PII spans boundaries but stays within the 50-char overlap window.
# Each chunk is ~20-30 chars so the rolling buffer accumulates enough to catch splits.
RESPONSE_CHUNKS = [
    "Sure! Here are some customer details:\n",
    "Customer: John Smith\nEmail: john.sm",    # email starts here
    "ith@example.com\nSSN: 123-45",            # email ends, SSN starts
    "-6789\nPhone: (555) 123-4567\n",          # SSN ends, phone complete
    "Credit card: 4111 1111 1111 1111\n",      # CC complete in one chunk
    "Second customer: jane@doe.org\n",          # email complete
    "Her phone: 555.987.6543\n",               # phone complete
    "Card on file: 5500-0000-0000-0004\n",     # card complete
    "\nLet me know if you need anything else!",
]


async def stream_response():
    for chunk in RESPONSE_CHUNKS:
        payload = json.dumps({
            "choices": [{"delta": {"content": chunk}}]
        })
        yield f"data: {payload}\n\n"
        await asyncio.sleep(0.05)   # simulate real streaming delay
    yield "data: [DONE]\n\n"


@app.post("/v1/chat/completions")
async def completions(request: Request):
    return StreamingResponse(
        stream_response(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=9100, log_level="warning")
