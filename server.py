"""Private stdio MCP entrypoint using the installed MCP 2 SDK."""
import asyncio
import json
import logging
import sys
import hashlib

import runtime
import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from backend import Bridge, INSTRUCTIONS
from computer_result import ComputerResult


def client_scope(meta):
    """Conversation correlation only; never use client metadata as authorization."""
    if hasattr(meta, 'model_dump'):
        meta = meta.model_dump(by_alias=True)
    if not isinstance(meta, dict) or not isinstance(meta.get('openai/session'), str) or not meta['openai/session']:
        return None
    identity = [meta.get(key) for key in ('openai/organization', 'openai/subject', 'openai/session')]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()


async def main():
    bridge = Bridge()

    async def list_tools(ctx, params):
        return types.ListToolsResult(tools=[types.Tool(**spec) for spec in bridge.specs.values()])

    async def call_tool(ctx, params):
        try:
            result = await asyncio.to_thread(bridge.call, params.name, params.arguments or {},
                                            client_scope=client_scope(ctx.meta) or f'transport:{id(ctx.session._connection)}')
            images = []
            if isinstance(result, ComputerResult):
                images, result = result.images, result.payload
            error = isinstance(result, dict) and (result.get('success') is False or result.get('ok') is False
                                                    or bool(result.get('error')))
            content = [types.TextContent(type='text', text=json.dumps(result, ensure_ascii=False))]
            content.extend(types.ImageContent(type='image', data=data, mimeType=mime) for mime, data in images)
            return types.CallToolResult(content=content,
                                        structured_content=result, is_error=error)
        except ValueError as exc:
            return types.CallToolResult(content=[types.TextContent(type='text', text=str(exc))], is_error=True)
        except Exception:
            logging.error('MCP tool failed: %s', params.name)
            return types.CallToolResult(content=[types.TextContent(type='text', text='工具執行失敗；結果未確認，勿自動重送。')], is_error=True)

    server = Server('Hermes Local Tools', version='0.1.0', instructions=INSTRUCTIONS,
                    on_list_tools=list_tools, on_call_tool=call_tool)
    try:
        async with stdio_server() as (reader, writer):
            await server.run(reader, writer, server.create_initialization_options())
    finally:
        bridge.close()


if __name__ == '__main__':
    logging.basicConfig(stream=sys.stderr, level=logging.ERROR)
    asyncio.run(main())
