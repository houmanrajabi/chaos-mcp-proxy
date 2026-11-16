"""
calls all three tools and shows the effect of each configured fault.

    uv run python client.py           # via proxy (faults active)
    uv run python client.py --direct  # direct to server (clean baseline)
"""

import argparse
import time

import anyio
from mcp import ClientSession, StdioServerParameters, stdio_client


def _separator(title: str) -> None:
    width = 62
    print(f"\n{'─' * width}")
    print(f"  {title}")
    print(f"{'─' * width}")


def _indent(text: str, n: int = 4) -> str:
    pad = " " * n
    return "\n".join(pad + line for line in text.splitlines())


def _extract_text(result) -> str:
    """first text block from a tool result."""
    for block in result.content:
        if hasattr(block, "text"):
            return block.text
    return "<no text content>"


async def run_demo(direct: bool = False) -> None:
    if direct:
        server_params = StdioServerParameters(
            command="python",
            args=["server.py"],
        )
        print("[demo] Mode: DIRECT (no proxy, no fault injection)")
    else:
        server_params = StdioServerParameters(
            command="python",
            args=["proxy.py", "--config", "config.yaml", "--", "python", "server.py"],
        )
        print("[demo] Mode: PROXY (fault injection active)")
        print("[demo] Fault log → demo_run.jsonl")

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # 1. calculator — perturb fault
            _separator("Tool: calculator | Expected fault: perturb (±3% numeric)")
            expr = "12500 * 1.07"
            correct = 12500 * 1.07
            print(f"  Expression : {expr!r}")
            print(f"  Correct    : {correct}")
            t0 = time.monotonic()
            try:
                result = await session.call_tool("calculator", {"expression": expr})
                elapsed = time.monotonic() - t0
                returned = _extract_text(result)
                print(f"  Returned   : {returned}  ({elapsed:.3f}s)")
                try:
                    diff_pct = abs(float(returned) - correct) / correct * 100
                    if diff_pct > 0.01:
                        print(f"  ⚠  Deviation: {diff_pct:.2f}% (plausible-wrong fault active)")
                    else:
                        print(f"  ✓  Result exact (direct mode or fault not triggered)")
                except ValueError:
                    print(f"  ✗  Non-numeric result: {returned!r}")
            except Exception as e:
                print(f"  ✗  Exception: {e}")

            # 2. fetch_page — substitute fault (B3: stale cache)
            _separator("Tool: fetch_page  | Expected fault: substitute (stale FY2024 data)")
            url = "https://investor.apple.com/sec-filings/annual-reports/"
            correct_revenue = "$391,035M"
            print(f"  URL        : {url!r}")
            print(f"  Correct    : FY2025 revenue = {correct_revenue}")
            t0 = time.monotonic()
            try:
                result = await session.call_tool("fetch_page", {"url": url})
                elapsed = time.monotonic() - t0
                text = _extract_text(result)
                if result.is_error:
                    print(f"  ✗  Tool-level error ({elapsed:.3f}s): {text}")
                elif "FY2024" in text or "383,285" in text:
                    print(f"  Stale data returned ({elapsed:.3f}s): substitute fault active")
                    print(f"  Returned:\n{_indent(text)}")
                else:
                    print(f"  ✓  current data ({elapsed:.3f}s):\n{_indent(text)}")
            except Exception as e:
                elapsed = time.monotonic() - t0
                print(f"  ✗  exception ({elapsed:.3f}s): {e}")

            # 3. web_search — latency fault
            _separator("Tool: web_search  | Expected fault: 2 000 ms latency")
            query = "Apple Microsoft Alphabet FY2025 annual revenue comparison"
            print(f"  Query      : {query!r}")
            if not direct:
                print(f"  waiting, 2s delay injected by proxy...")
            t0 = time.monotonic()
            try:
                result = await session.call_tool("web_search", {"query": query})
                elapsed = time.monotonic() - t0
                text = _extract_text(result)
                print(f"  Returned in {elapsed:.3f}s:")
                print(_indent(text))
                if elapsed >= 1.8:
                    print(f"  ⚠  Latency fault confirmed ({elapsed:.2f}s ≥ 2s target)")
                else:
                    print(f"  ✓  No latency fault (direct mode or fault not triggered)")
            except Exception as e:
                elapsed = time.monotonic() - t0
                print(f"  ✗  Exception ({elapsed:.3f}s): {e}")

    print(f"\n{'─' * 62}")
    print("  Demo complete.")
    if not direct:
        print("  Fault log → demo_run.jsonl")
        print("  Each injected fault is one JSON line with timestamp,")
        print("  tool name, fault type, and original/injected values.")
    print(f"{'─' * 62}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Demo client for chaos-mcp-proxy")
    parser.add_argument(
        "--direct",
        action="store_true",
        help="Connect directly to server.py (no proxy, shows clean baseline)",
    )
    args = parser.parse_args()
    anyio.run(run_demo, args.direct)


if __name__ == "__main__":
    main()
