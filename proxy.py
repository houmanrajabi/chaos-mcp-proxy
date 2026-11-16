"""
fault-injecting mcp proxy at the json-rpc wire level.

sits between an mcp client (stdin/stdout) and a backend mcp server subprocess.
intercepts tool/call responses and applies configured faults before forwarding.
all other messages are relayed unchanged.

usage:
    uv run python proxy.py --config config.yaml -- python server.py
"""

import argparse
import asyncio
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


class FaultEngine:
    def __init__(self, config: dict, log_path: Path) -> None:
        self.rules: dict[str, dict] = {
            r["tool"]: r for r in config.get("faults", [])
        }
        self.rng = random.Random(config.get("seed", 42))
        self.log_path = log_path

    async def apply(self, tool_name: str, response: dict) -> dict:
        """apply fault rule to response, or return unchanged."""
        rule = self.rules.get(tool_name)
        if rule is None:
            return response

        if self.rng.random() > rule.get("rate", 1.0):
            return response  # rate check: not faulted this call

        fault = rule["fault"]

        if fault == "latency":
            delay_s = rule.get("delay_ms", 2000) / 1000.0
            await asyncio.sleep(delay_s)
            self._log(tool_name, fault, f"added {delay_s:.2f}s delay", None, None)
            return response

        elif fault == "error":
            self._log(tool_name, fault, "replaced with JSON-RPC error",
                      None, rule.get("code", -32000))
            return {
                "jsonrpc": "2.0",
                "id": response.get("id"),
                "error": {
                    "code": rule.get("code", -32000),
                    "message": rule.get("message", "Injected: server unavailable"),
                },
            }

        elif fault == "perturb":
            response = self._perturb_numeric(tool_name, response, rule)
            return response

        elif fault == "substitute":
            return self._substitute_body(tool_name, response, rule)

        # unknown fault type, pass through
        return response

    def _perturb_numeric(self, tool_name: str, response: dict, rule: dict) -> dict:
        """perturb the first numeric text content by a gaussian multiplicative factor."""
        try:
            content = response["result"]["content"]
        except (KeyError, TypeError):
            return response  # no content block, pass through

        magnitude = rule.get("magnitude", 0.03)
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "text":
                continue
            raw = item["text"].strip()
            try:
                value = float(raw)
            except ValueError:
                continue  # non-numeric item, skip
            perturbation = 1.0 + self.rng.gauss(0, magnitude)
            new_value = value * perturbation
            item["text"] = f"{new_value:.4f}"
            self._log(tool_name, "perturb", "numeric perturbation", raw, item["text"])
            break  # only perturb the first numeric value

        return response

    def _substitute_body(self, tool_name: str, response: dict, rule: dict) -> dict:
        """replace the first text content block with a pre-configured body string."""
        body = rule.get("body", "")
        try:
            content = response["result"]["content"]
        except (KeyError, TypeError):
            return response
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                original = item["text"][:80]
                item["text"] = body
                self._log(tool_name, "substitute", "content body replaced", original, body[:80])
                break
        return response

    def _log(
        self,
        tool: str,
        fault: str,
        action: str,
        original: str | None,
        injected: Any,
    ) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "tool": tool,
            "fault": fault,
            "action": action,
            "original": original,
            "injected": str(injected) if injected is not None else None,
        }
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
        # stderr so it doesn't interfere with the jsonrpc stream
        print(
            f"[proxy fault] {fault:10s} | {tool:15s} | {action}",
            file=sys.stderr,
            flush=True,
        )


class MCPFaultProxy:
    def __init__(self, config: dict, backend_cmd: list[str], log_path: Path) -> None:
        self.backend_cmd = backend_cmd
        self.engine = FaultEngine(config, log_path)
        # track pending tool calls to match responses by request id
        self._pending: dict[int | str, str] = {}

    async def run(self) -> None:
        proc = await asyncio.create_subprocess_exec(
            *self.backend_cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.gather(
                self._client_to_backend(proc),
                self._backend_to_client(proc),
            )
        finally:
            proc.terminate()
            await proc.wait()

    async def _client_to_backend(self, proc: asyncio.subprocess.Process) -> None:
        """forward client messages to backend, tracking tool call ids."""
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer
        )
        while True:
            line = await reader.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                proc.stdin.write(line)
                await proc.stdin.drain()
                continue

            # track outbound tool calls by request id
            if msg.get("method") == "tools/call" and "id" in msg:
                tool_name = msg.get("params", {}).get("name", "")
                self._pending[msg["id"]] = tool_name

            proc.stdin.write(line)
            await proc.stdin.drain()

    async def _backend_to_client(self, proc: asyncio.subprocess.Process) -> None:
        """forward backend responses to client, injecting faults on tool call results."""
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                sys.stdout.buffer.write(line)
                sys.stdout.buffer.flush()
                continue

            msg_id = msg.get("id")
            if msg_id is not None and msg_id in self._pending:
                tool_name = self._pending.pop(msg_id)
                # don't double-fault already-errored responses
                if "result" in msg or "error" not in msg:
                    msg = await self.engine.apply(tool_name, msg)

            out = json.dumps(msg).encode("utf-8") + b"\n"
            sys.stdout.buffer.write(out)
            sys.stdout.buffer.flush()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="fault-injecting proxy between an MCP client and server subprocess"
    )
    parser.add_argument("--config", required=True, help="YAML fault configuration file")
    parser.add_argument(
        "--log", default="demo_run.jsonl", help="JSONL injection log (appended)"
    )
    parser.add_argument(
        "server_args",
        nargs=argparse.REMAINDER,
        help="Command to start the backend MCP server (after --)",
    )
    args = parser.parse_args()

    # strip the -- separator if used
    server_cmd = args.server_args
    if server_cmd and server_cmd[0] == "--":
        server_cmd = server_cmd[1:]

    if not server_cmd:
        parser.error("Provide the backend server command after --")

    with open(args.config, encoding="utf-8") as fh:
        config = yaml.safe_load(fh)

    proxy = MCPFaultProxy(config, server_cmd, Path(args.log))
    asyncio.run(proxy.run())


if __name__ == "__main__":
    main()
