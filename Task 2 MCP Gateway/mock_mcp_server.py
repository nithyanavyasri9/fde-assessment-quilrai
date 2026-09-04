"""
Mock downstream MCP server — simulates what the gateway proxies TO.
Runs on port 9001. Exposes both regular and admin tools.

Start this FIRST before starting the gateway:
    python mock_mcp_server.py
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

app = FastAPI(title="Mock Downstream MCP Server")

TOOLS = [
    {"name": "get_customer_record",  "description": "Get a customer record"},
    {"name": "trigger_refund",       "description": "Trigger a refund"},
    {"name": "admin_reset_key",      "description": "[ADMIN] Reset an API key"},
    {"name": "admin_delete_account", "description": "[ADMIN] Delete a customer account"},
]

@app.post("/mcp")
async def handle_mcp(request: Request):
    body = await request.json()
    method = body.get("method")
    req_id = body.get("id", 1)

    if method == "tools/list":
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": TOOLS}
        })

    if method == "tools/call":
        tool_name = body.get("params", {}).get("name", "unknown")
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [{"type": "text", "text": f"[DOWNSTREAM] Tool '{tool_name}' executed successfully."}]
            }
        })

    return JSONResponse({
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"}
    })

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=9001, log_level="warning")
