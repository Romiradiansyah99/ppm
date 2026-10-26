"""FastMCP server exposing PPM's role-scoped analyst tools to Hermes."""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from ppm.mcp import domain

mcp = FastMCP("ppm")


@mcp.tool()
def schema_catalog(context: str) -> str:
    """List analyst views and columns visible to this PPM role. Pass the signed context token."""
    return domain.schema_catalog(context)


@mcp.tool()
def sql_query(context: str, sql: str) -> str:
    """Run a read-only SELECT/WITH query over analyst_* views. Pass the signed context token."""
    return domain.sql_query(context, sql)


@mcp.tool()
def document_search(context: str, question: str) -> str:
    """Search the cited template and procedure corpus. Pass the signed context token."""
    return domain.document_search(context, question)


@mcp.tool()
def plan_and_run(context: str, question: str) -> str:
    """Plan and execute a multi-step project-cost/risk/change analysis. Pass the signed context token."""
    return domain.plan_and_run(context, question)


@mcp.tool()
def find_templates(context: str, query: str) -> str:
    """Find office templates by code or title. Pass the signed context token."""
    return domain.find_templates(context, query)


@mcp.tool()
def create_document(
    context: str,
    template: str,
    project: str = "",
    fields_json: str = "{}",
    auto_pick: bool = False,
) -> str:
    """Create a DRAFT document for the current user and conversation. Pass the signed context token."""
    return domain.create_document(context, template, project, fields_json, auto_pick)


@mcp.tool()
def remember_preference(context: str, content: str, kind: str = "preference") -> str:
    """Save a durable preference or fact for the current user. Pass the signed context token."""
    return domain.remember_preference(context, content, kind)


@mcp.tool()
def corpus_stats(context: str) -> str:
    """Return corpus document, chunk, template and ingest counts. Pass the signed context token."""
    return domain.corpus_stats(context)


@mcp.tool()
def run_cost_demo(context: str, question: str) -> str:
    """Run the deterministic benchmark demo (admin only). Pass the signed context token."""
    return domain.run_cost_demo(context, question)


def main() -> None:
    host = os.environ.get("PPM_MCP_HOST", "0.0.0.0").strip()
    port = int(os.environ.get("PPM_MCP_PORT", "8765"))
    mcp.settings.host = host
    mcp.settings.port = port
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
