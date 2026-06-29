"""FastMCP adapter for mrag."""
from __future__ import annotations

from typing import Any

from mrag.config.mcp import EffectiveMcpConfig
from mrag.mcp.resources import (
    chunk_resource,
    document_resource,
    documents_resource,
    extracted_resource,
    kb_info_resource,
    profile_resource,
    profiles_resource,
)
from mrag.mcp.tools import (
    McpToolContext,
    inspect_chunk_tool,
    inspect_chunks_tool,
    inspect_document_tool,
    inspect_sections_tool,
    list_documents_tool,
    list_profiles_tool,
    search_tool,
)


def _import_fastmcp():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - exercised when optional dep absent
        raise RuntimeError(
            "MCP support is not installed. Install with: uv pip install -e \".[mcp]\""
        ) from exc
    return FastMCP


class BearerAuthMiddleware:
    def __init__(self, app, token: str | None, allowed_origins: list[str]) -> None:
        self.app = app
        self.token = token
        self.allowed_origins = set(allowed_origins)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin1").lower(): value.decode("latin1")
            for key, value in scope.get("headers", [])
        }
        origin = headers.get("origin")
        if self.allowed_origins and origin and origin not in self.allowed_origins:
            await self._send(send, 403, b"Forbidden origin")
            return

        if self.token:
            auth = headers.get("authorization", "")
            if auth != f"Bearer {self.token}":
                await self._send(send, 401, b"Unauthorized")
                return

        await self.app(scope, receive, send)

    async def _send(self, send, status: int, body: bytes) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def build_fastmcp(effective: EffectiveMcpConfig):
    FastMCP = _import_fastmcp()
    raw = effective.raw
    ctx = McpToolContext(effective)

    server = FastMCP(
        "mrag",
        instructions=(
            "Read-only MCP server for an mrag knowledge base. "
            "Use search for retrieval and inspect tools/resources for source details."
        ),
        log_level=raw.logging.level,
        host=raw.http.host,
        port=raw.http.port,
        streamable_http_path=raw.http.path,
    )

    if raw.features.tools:
        @server.tool(description="Search the mrag knowledge base.")
        def search(
            query: str,
            profile: str | None = None,
            strategy: str | None = None,
            top_k: int | None = None,
            no_rerank: bool | None = None,
        ) -> dict[str, Any]:
            return search_tool(
                ctx,
                query=query,
                profile=profile,
                strategy=strategy,
                top_k=top_k,
                no_rerank=no_rerank,
            )

        @server.tool(description="List documents registered in the mrag project.")
        def list_documents(limit: int | None = None, offset: int = 0) -> dict[str, Any]:
            return list_documents_tool(ctx, limit=limit, offset=offset)

        @server.tool(description="List retrieval profiles in the mrag project.")
        def list_profiles() -> dict[str, Any]:
            return list_profiles_tool(ctx)

        @server.tool(description="Inspect one document's indexing status.")
        def inspect_document(
            document_id: str,
            profile: str | None = None,
        ) -> dict[str, Any]:
            return inspect_document_tool(ctx, document_id=document_id, profile=profile)

        @server.tool(description="List chunks for a document.")
        def inspect_chunks(
            document_id: str,
            profile: str | None = None,
            limit: int | None = None,
            offset: int = 0,
            show_content: bool = False,
            show_context: bool = False,
        ) -> dict[str, Any]:
            return inspect_chunks_tool(
                ctx,
                document_id=document_id,
                profile=profile,
                limit=limit,
                offset=offset,
                show_content=show_content,
                show_context=show_context,
            )

        @server.tool(description="Inspect one chunk including content and context.")
        def inspect_chunk(chunk_id: str) -> dict[str, Any]:
            return inspect_chunk_tool(ctx, chunk_id=chunk_id)

        @server.tool(description="Inspect document sections or parent-child hierarchy.")
        def inspect_sections(
            document_id: str,
            profile: str | None = None,
        ) -> dict[str, Any]:
            return inspect_sections_tool(ctx, document_id=document_id, profile=profile)

    if raw.features.resources:
        @server.resource("mrag://kb/info", mime_type="text/yaml")
        def kb_info() -> str:
            return kb_info_resource(ctx)

        @server.resource("mrag://profiles", mime_type="application/json")
        def profiles() -> str:
            return profiles_resource(ctx)

        @server.resource("mrag://profiles/{profile}", mime_type="text/yaml")
        def profile(profile: str) -> str:
            return profile_resource(ctx, profile)

        @server.resource("mrag://documents", mime_type="application/json")
        def documents() -> str:
            return documents_resource(ctx)

        @server.resource("mrag://documents/{document_id}", mime_type="application/json")
        def document(document_id: str) -> str:
            return document_resource(ctx, document_id)

        @server.resource("mrag://documents/{document_id}/extracted.txt", mime_type="text/plain")
        def extracted_txt(document_id: str) -> str:
            return extracted_resource(ctx, document_id, "txt")

        @server.resource("mrag://documents/{document_id}/extracted.md", mime_type="text/markdown")
        def extracted_md(document_id: str) -> str:
            return extracted_resource(ctx, document_id, "md")

        @server.resource("mrag://chunks/{chunk_id}", mime_type="application/json")
        def chunk(chunk_id: str) -> str:
            return chunk_resource(ctx, chunk_id)

    return server


def run_mcp_server(effective: EffectiveMcpConfig) -> None:
    server = build_fastmcp(effective)
    raw = effective.raw
    if raw.transport == "stdio":
        server.run(transport="stdio")
        return

    # Wrap the SDK's Starlette app so auth/origin checks are kept in mrag's
    # config model instead of relying on SDK-specific auth helpers.
    import uvicorn

    app = server.streamable_http_app()
    app = BearerAuthMiddleware(app, effective.auth_token, raw.http.allowed_origins)
    uvicorn.run(
        app,
        host=raw.http.host,
        port=raw.http.port,
        log_level=raw.logging.level.lower(),
    )


__all__ = ["build_fastmcp", "run_mcp_server"]
