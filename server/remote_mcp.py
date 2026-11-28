"""Stateless MCP transport for the same read-only queries as the Monitor API."""
import os
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from .db import db
from .domains.content import list_records
from .domains.integrations import overview, query_dataset


hosts = {"localhost", "127.0.0.1", "[::1]", "47.79.224.196"}
hosts.update(value for key in ("DOMAIN", "RAILWAY_PUBLIC_DOMAIN") if (value := os.getenv(key)))
mcp = FastMCP(
    "monitorhub",
    stateless_http=True,
    json_response=True,
    streamable_http_path="/mcp",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[entry for host in hosts for entry in (host, f"{host}:*")],
        allowed_origins=[f"{scheme}://{entry}" for scheme in ("http", "https")
                         for host in hosts for entry in (host, f"{host}:*")],
    ),
)
Limit = Annotated[int, Field(ge=1, le=200)]


def _read(query, **filters):
    with db() as conn:
        conn.execute("PRAGMA query_only=ON")
        return query(conn=conn, **filters)


@mcp.tool()
async def monitor_overview() -> dict[str, Any]:
    """Get MonitorHub dataset counts and the latest record timestamp."""
    return await run_in_threadpool(_read, overview)


@mcp.tool()
async def monitor_brands(limit: Limit = 200) -> dict[str, Any]:
    """List monitored brands."""
    return await run_in_threadpool(_read, query_dataset, dataset="brands", limit=limit)


@mcp.tool()
async def monitor_records(brand_id: str | None = None, q: str | None = None,
                          start_date: str | None = None, end_date: str | None = None,
                          days: int | None = None, limit: Limit = 50) -> dict[str, Any]:
    """Search unified MonitorHub records."""
    return await run_in_threadpool(_read, list_records, brand_id=brand_id, q=q, days=days,
                                   start_date=start_date, end_date=end_date, limit=limit)


@mcp.tool()
async def monitor_query(dataset: str, brand_id: str | None = None, q: str | None = None,
                        start_date: str | None = None, end_date: str | None = None,
                        limit: Limit = 50) -> dict[str, Any]:
    """Query a read-only MonitorHub dataset."""
    return await run_in_threadpool(_read, query_dataset, dataset=dataset, brand_id=brand_id,
                                   q=q, start_date=start_date, end_date=end_date, limit=limit)


http_app = mcp.streamable_http_app()
