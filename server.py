"""
minimal mcp server with the three tools the proxy targets: web_search, fetch_page,
and calculator. all tool responses are canned stubs for demo purposes.

usage:
    uv run python server.py
    uv run python proxy.py --config config.yaml -- python server.py
"""

import ast
import operator
import anyio
from mcp.server.mcpserver import MCPServer

# canned stub data

WEB_SEARCH_RESULTS = (
    "FY2025 Revenue Summary:\n"
    "  Apple Inc. (AAPL):       $391,035M  (+2.1% YoY from $383,285M)\n"
    "  Microsoft Corp. (MSFT):  $272,003M  (+15.7% YoY from $235,012M)\n"
    "  Alphabet Inc. (GOOGL):   $350,018M  (+12.3% YoY from $307,394M)\n"
    "Source: company 10-K filings, SEC EDGAR, retrieved 2026-11-16"
)

FETCH_PAGE_CONTENT = (
    "Apple Inc. — FY2025 Annual Report (10-K Extract)\n"
    "Total net revenue:  $391,035M\n"
    "Products revenue:   $247,890M\n"
    "Services revenue:   $143,145M\n"
    "Net income:          $93,736M\n"
    "EPS (diluted):         $6.08\n"
    "YoY revenue growth:    +2.1%\n"
)

# safe arithmetic eval using ast, no exec() or eval()

_SAFE_OPS: dict = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _safe_eval(expr: str) -> float:
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as e:
        raise ValueError(f"Invalid expression: {e}") from e

    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.BinOp):
            op = _SAFE_OPS.get(type(node.op))
            if op is None:
                raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
            return op(_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp):
            op = _SAFE_OPS.get(type(node.op))
            if op is None:
                raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
            return op(_eval(node.operand))
        raise ValueError(f"Unsupported node type: {type(node).__name__}")

    return _eval(tree)


app = MCPServer("toy-reportcrew-server")


@app.tool(description=(
    "Search the web for financial data. "
    "Returns FY2025 revenue figures for Apple, Microsoft, Alphabet."
))
def web_search(query: str) -> str:
    return f"Query: {query!r}\n\n{WEB_SEARCH_RESULTS}"


@app.tool(description=(
    "Fetch the content of a web page. "
    "Returns a canned Apple FY2025 10-K extract."
))
def fetch_page(url: str) -> str:
    return f"URL: {url}\n\n{FETCH_PAGE_CONTENT}"


@app.tool(description="Evaluate a numeric arithmetic expression (+, -, *, /, **).")
def calculator(expression: str) -> str:
    try:
        result = _safe_eval(expression)
        return str(result)
    except (ValueError, ZeroDivisionError) as e:
        return f"Error: {e}"


if __name__ == "__main__":
    anyio.run(app.run_stdio_async)
