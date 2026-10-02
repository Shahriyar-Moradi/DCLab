"""DCLab MCP server over the /v1 SDK (``dclab_client``); see ``dclab_mcp.server``."""

from dclab_mcp._version import __version__
from dclab_mcp.server import ConfigError, Settings, build_server, main, settings_from_env

__all__ = ["ConfigError", "Settings", "__version__", "build_server", "main", "settings_from_env"]
