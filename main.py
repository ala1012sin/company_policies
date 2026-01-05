from fastapi import FastAPI, HTTPException
from fastmcp import FastMCP
import json
import os
from pdf_chunking import collection

mcp = FastMCP("MES-MCP")
mcp_app = mcp.http_app()

app = FastAPI(
    title="MES API + MCP",
    description="REST API와 MCP를 동시에 제공하는 통합 서버",
    version="1.0.0",
    lifespan=mcp_app.lifespan
)


@mcp.tool()
def searcing_chromadb(query: str, top_k: int = 5):
    """ChromaDB에서 회사 내규 문서를 검색합니다."""
    results = collection.query(
        query_texts=[query],
        n_results=top_k,
        include=["documents", "metadatas"]
    )
    response = []
    for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
        response.append({
            "document": doc,
            "metadata": meta
        })
    return json.dumps(response, ensure_ascii=False, indent=2)




app.mount("/", mcp_app)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
