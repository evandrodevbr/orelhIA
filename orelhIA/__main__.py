"""Entrypoint do MCP server: ``python -m orelhIA`` (stdio).

Encaminha para o ``mcp.run()`` canônico definido em ``server.py``.
"""
import sys
from pathlib import Path

# server.py fica na raiz do repo, um nível acima deste package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import mcp

if __name__ == "__main__":
    mcp.run()
