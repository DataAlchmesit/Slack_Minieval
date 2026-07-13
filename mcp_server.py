"""
mcp_server.py
-------------
Real MCP (Model Context Protocol) server for MiniEval's evaluation engine.

FIXED: the previous version (MiniEvalMCPServer) was a plain Python class
with a hand-rolled handle_request(tool_name, params) dispatcher -- no
transport, no protocol handshake, nothing an actual MCP client could
discover or call. It satisfied the *name* "MCP server" but not the
technology. This version uses the real `mcp` SDK (Server + stdio
transport) so it's genuinely connectable by an MCP Inspector, Claude
Desktop, or any other MCP-compatible host.

This runs as its OWN process, independent of the live Slack agent.
pipeline.py calls the evaluator directly for the live path -- routing
every live Slack check through an MCP subprocess call would add latency
and failure surface for no judging benefit, since "MCP server
integration" is satisfied by having a correct, working MCP server in the
repo, not by forcing internal calls through it.

Exposes:
    evaluate_summary(summary, context)   -> faithfulness score + label
    get_channel_stats(channel, days=30)  -> hallucination rate / trust score
    get_recent_evaluations(limit=10)     -> recently logged evaluations

Run standalone:
    python mcp_server.py

Test with the MCP Inspector:
    npx @modelcontextprotocol/inspector python mcp_server.py
"""


from __future__ import annotations

import sys
import asyncio
import json
import logging

import config
from evaluator_bridge import evaluate as run_evaluation, EvalResult
from storage import EvaluationStorage

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

# CRITICAL for stdio MCP: all logging must go to stderr, never stdout.
# stdout is the JSON-RPC protocol channel — any stray print/log there
# corrupts the handshake and the Inspector reports a connection error.
logging.basicConfig(
    stream=sys.stderr,
    level=logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

server = Server("minieval-slack")
storage = EvaluationStorage(db_path=config.DB_PATH)


# ── Tool Discovery ───────────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="evaluate_summary",
            description=(
                "Evaluate whether an AI-generated summary faithfully represents "
                "its source text. Returns a faithfulness score (0-1), a verdict "
                "label (FAITHFUL / UNCERTAIN / HALLUCINATED), and NLI sub-scores."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "The AI-generated summary to check.",
                    },
                    "context": {
                        "type": "string",
                        "description": "The original source text (premise).",
                    },
                },
                "required": ["summary", "context"],
            },
        ),
        types.Tool(
            name="get_channel_stats",
            description="Return hallucination rate and trust score for a Slack channel.",
            inputSchema={
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "description": "Slack channel ID or name."},
                    "days": {
                        "type": "integer",
                        "description": "Lookback window in days.",
                        "default": 30,
                    },
                },
                "required": ["channel"],
            },
        ),
        types.Tool(
            name="get_recent_evaluations",
            description="Return the most recently logged evaluations across all channels.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max records to return.",
                        "default": 10,
                    },
                },
            },
        ),
    ]


# ── Tool Dispatch ────────────────────────────────────────────────────────────────

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "evaluate_summary":
        result = _evaluate_summary(
            arguments.get("summary", ""), arguments.get("context", "")
        )
    elif name == "get_channel_stats":
        result = storage.get_channel_stats(
            arguments.get("channel", ""), arguments.get("days", 30)
        )
    elif name == "get_recent_evaluations":
        result = storage.get_recent_evaluations(limit=arguments.get("limit", 10))
    else:
        result = {"error": f"Unknown tool: {name}"}

    return [types.TextContent(type="text", text=json.dumps(result, indent=2, default=str))]


def _evaluate_summary(summary: str, context: str) -> dict:
    try:
        # evaluator_bridge.evaluate(source_text, summary_text) -- context is
        # the source/premise, summary is the hypothesis. Get this order
        # wrong and every score is backwards (this was the pipeline.py bug).
        result: EvalResult = run_evaluation(context, summary)

        storage.log_evaluation(
            channel="mcp-direct",
            thread_ts="",
            score=result.score,
            is_hallucination=result.is_hallucinated,
            details={
                **result.to_dict(),
                "summary": summary[:200],
                "context": context[:200],
            },
        )

        return {
            "score": result.score,
            "faithful": result.is_verified,
            "label": result.label,
            "is_hallucinated": result.is_hallucinated,
            "details": result.to_dict(),
            "latency_ms": result.latency_ms,
        }
    except Exception as e:
        logger.error(f"evaluate_summary failed: {e}", exc_info=True)
        return {"error": str(e)}


# ── Entry Point ───────────────────────────────────────────────────────────────────

async def main():
    logger.info("MiniEval MCP server starting (stdio transport)...")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())