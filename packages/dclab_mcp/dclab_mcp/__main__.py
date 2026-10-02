"""``python -m dclab_mcp`` runs the stdio MCP server."""

import sys

from dclab_mcp.server import main

if __name__ == "__main__":
    sys.exit(main())
