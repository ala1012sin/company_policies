from fastapi import FastAPI, HTTPException
from fastmcp import FastMCP
import json
import os

mcp = FastMCP("MES-MCP")
mcp_app = mcp.http_app()

app = FastAPI(
    title="MES API + MCP",
    description="REST API와 MCP를 동시에 제공하는 통합 서버",
    version="1.0.0",
    lifespan=mcp_app.lifespan
)


app.mount("/", mcp_app)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
