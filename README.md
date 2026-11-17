# chaos-mcp-proxy

Fault-injecting proxy for stdio MCP servers. Sits between a client and a backend server
subprocess, intercepts `tools/call` responses, and applies configured faults before
forwarding.

```
client (client.py)
        │
   [proxy.py]   ← fault injection
        │
server (server.py, or any real MCP server)
```

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

## Run the demo

```bash
uv run python client.py           # via proxy (faults active)
uv run python client.py --direct  # direct to server (clean baseline)
```

Each injected fault is logged to `demo_run.jsonl`.

## Run the proxy manually

```bash
uv run python proxy.py --config config.yaml -- python server.py
```

## Configuration

Edit `config.yaml` to change which tools are faulted and how. Three fault types:

- `perturb` — multiplies the first numeric result by a Gaussian factor
- `substitute` — replaces the response's text content block with a pre-stored body string
- `error` — replaces the response with a JSON-RPC error object
- `latency` — sleeps before forwarding the response

`seed` fixes the PRNG so the same config always produces the same fault schedule.

## Limitations

- stdio only, does not support SSE or HTTP transport
- cannot inject faults at inter-agent message boundaries (framework-level instrumentation needed)
- `perturb` skips non-scalar results silently
