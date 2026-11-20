"""MCP must distinguish omitted limit from an explicit 20 (real stdio + CLI)."""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import server

REPO = Path(__file__).resolve().parent.parent


class SearchLimitTests(unittest.TestCase):
    def test_forwarding_and_trace_preserve_omission_and_explicit_values(self):
        for limit in (None, 0, 19, 20, 21):
            with self.subTest(limit=limit), mock.patch.object(server, "_run", return_value="ok") as run:
                if limit is None:
                    server.search(query="alpha", json_output=True)
                else:
                    server.search(query="alpha", json_output=True, limit=limit)
                args = run.call_args.args[0]
                self.assertEqual(run.call_args.kwargs["params"]["limit"], limit)
                if limit is None:
                    self.assertNotIn("--limit", args)
                else:
                    self.assertEqual(args[args.index("--limit") + 1], str(limit))

    def test_real_mcp_json_and_table_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / ".okf.config.yaml").write_text(
                "types:\n  valid: [Insight]\nfeatures:\n  cognitive_trace: false\n", encoding="utf-8")
            for i in range(25):
                (vault / f"n{i:02}.md").write_text(
                    f"---\ntype: Insight\ntitle: Alpha {i}\ndescription: D\n---\nBody.\n", encoding="utf-8")
            asyncio.run(self._check_mcp(vault))

    async def _check_mcp(self, vault):
        env = {**os.environ, "PYTHONPATH": str(REPO), "OKF_VAULT": str(vault),
               "OKF_SESSION_PURPOSE": "test", "OKF_SCOPE": "", "OKF_READONLY": "false"}
        params = StdioServerParameters(command=sys.executable,
                                      args=[str(REPO / "server.py"), "--vault", str(vault)], env=env)
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                tools = await client.list_tools()
                schema = next(tool.inputSchema for tool in tools.tools if tool.name == "search")
                self.assertNotIn("limit", schema.get("required", []))

                for limit, expected in ((None, 25), (0, 25), (19, 19), (20, 20), (21, 21)):
                    with self.subTest(format="json", limit=limit):
                        args: dict = {"query": "alpha", "json_output": True}
                        if limit is not None:
                            args["limit"] = limit
                        result = await client.call_tool("search", args)
                        self.assertFalse(result.isError)
                        text = "".join(item.text for item in result.content if item.type == "text")
                        self.assertEqual(len(json.loads(text)), expected)
                for limit, expected in ((None, 20), (0, 25), (20, 20)):
                    with self.subTest(format="table", limit=limit):
                        args = {"query": "alpha"}
                        if limit is not None:
                            args["limit"] = limit
                        result = await client.call_tool("search", args)
                        self.assertFalse(result.isError)
                        text = "".join(item.text for item in result.content if item.type == "text")
                        rows = [line for line in text.splitlines()
                                if line.startswith("  ") and line.split() and line.split()[0].endswith(".md")]
                        self.assertEqual(len(rows), expected)
                self.assertIsNone(schema["properties"]["limit"].get("default"))


if __name__ == "__main__":
    unittest.main()
