"""
Mock primary and fallback LLM servers for Task 4 testing.
Run this single file — it starts BOTH servers simultaneously.

Primary  → port 9400 (can be set to return 429 via ?force_429=true)
Fallback → port 9401 (always succeeds)

Start: python mock_llm_servers.py
"""

import asyncio
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# ── Primary server (port 9400) ────────────────────────────────────────────────
primary = FastAPI(title="Mock Primary LLM")
primary_force_429 = False   # flip via /set_429?value=true

@primary.post("/v1/chat/completions")
async def primary_completions(request: Request):
    global primary_force_429
    if primary_force_429:
        return JSONResponse(status_code=429, content={"error": "rate limited"})
    body = await request.json()
    return JSONResponse({
        "id": "chatcmpl-primary-001",
        "model": "primary-model",
        "choices": [{"message": {"role": "assistant",
            "content": "Response from PRIMARY model."}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18}
    })

@primary.get("/set_429")
async def set_429(value: str = "true"):
    global primary_force_429
    primary_force_429 = value.lower() == "true"
    return {"primary_force_429": primary_force_429}

@primary.get("/health")
async def primary_health():
    return {"status": "ok", "server": "primary", "force_429": primary_force_429}


# ── Fallback server (port 9401) ───────────────────────────────────────────────
fallback = FastAPI(title="Mock Fallback LLM")

@fallback.post("/v1/chat/completions")
async def fallback_completions(request: Request):
    return JSONResponse({
        "id": "chatcmpl-fallback-001",
        "model": "fallback-model",
        "choices": [{"message": {"role": "assistant",
            "content": "Response from FALLBACK model."}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18}
    })

@fallback.get("/health")
async def fallback_health():
    return {"status": "ok", "server": "fallback"}


# ── Run both servers concurrently ─────────────────────────────────────────────
async def main():
    config_primary  = uvicorn.Config(primary,  host="127.0.0.1", port=9400, log_level="warning")
    config_fallback = uvicorn.Config(fallback, host="127.0.0.1", port=9401, log_level="warning")
    server_primary  = uvicorn.Server(config_primary)
    server_fallback = uvicorn.Server(config_fallback)
    print("[INFO] Primary LLM mock  → http://127.0.0.1:9400")
    print("[INFO] Fallback LLM mock → http://127.0.0.1:9401")
    print("[INFO] Force 429 on primary: GET http://127.0.0.1:9400/set_429?value=true")
    await asyncio.gather(server_primary.serve(), server_fallback.serve())

if __name__ == "__main__":
    asyncio.run(main())
