"""Connect to MATECOS over Streamable HTTP, list tools, and call one tool."""

from __future__ import annotations

import argparse
import asyncio
import json

from mcp import Client


async def run(endpoint: str, tool_name: str, arguments: dict) -> None:
    async with Client(endpoint) as client:
        result = await client.list_tools()
        print("Available MATECOS tools:")
        for tool in result.tools:
            print(f"- {tool.name}: {tool.description or '(no description)'}")

        selected = next((tool for tool in result.tools if tool.name == tool_name), None)
        if selected is None:
            raise SystemExit(f"Tool '{tool_name}' is not available from {endpoint}.")

        print(f"\nCalling {selected.name}...")
        call_result = await client.call_tool(selected.name, arguments)
        if call_result.is_error:
            print(call_result.content[0].text)
            raise SystemExit(1)
        if call_result.structured_content is not None:
            print(json.dumps(call_result.structured_content, ensure_ascii=False, indent=2))
        else:
            for item in call_result.content:
                if hasattr(item, "text"):
                    print(item.text)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("endpoint", nargs="?", default="http://127.0.0.1:8000/mcp")
    parser.add_argument("--tool", default="math.calculator")
    parser.add_argument("--arguments", default='{"expression":"2 + 2"}')
    args = parser.parse_args()
    asyncio.run(run(args.endpoint, args.tool, json.loads(args.arguments)))


if __name__ == "__main__":
    main()
