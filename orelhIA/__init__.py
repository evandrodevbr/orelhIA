"""orelhIA — a orelha (the ear) que seus agentes MCP sempre precisaram.

MCP server for local audio transcription via Parakeet TDT (PT-BR native).
This package re-exports the canonical implementation from server.py for
backwards compatibility with `python -m orelhIA` invocations.
"""
from server import (  # noqa: F401  -- intentional re-export
    __version__,
    mcp,
    health,
    transcribe_file,
    transcribe_url,
    record_audio,
    get_metrics,
    clear_cache,
    bootstrap_parakeet,
)

__version__ = __version__
__all__ = [
    "mcp",
    "health",
    "transcribe_file",
    "transcribe_url",
    "record_audio",
    "get_metrics",
    "clear_cache",
    "bootstrap_parakeet",
]
