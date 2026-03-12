import asyncio
from mcp.server.fastmcp import FastMCP
from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import Response

mcp = FastMCP("test", mount_path="/mcp")

@mcp.tool()
def hello() -> str:
    return "world"

app = FastAPI()
app.mount("/mcp", mcp.sse_app())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8912)
