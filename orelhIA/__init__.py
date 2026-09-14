"""orelhIA — a orelha (the ear) que seus agentes MCP sempre precisaram.

MCP server for local audio transcription via Parakeet TDT (PT-BR native).
This package re-exports the canonical implementation from server.py for
backwards compatibility with `python -m orelhIA` invocations.
"""
from server import (
    __version__,
    bootstrap_parakeet,
    clear_cache,
    get_metrics,
    health,
    mcp,
    record_audio,
    transcribe_file,
    transcribe_url,
)

__version__ = __version__
__all__ = [
    "bootstrap_parakeet",
    "clear_cache",
    "get_metrics",
    "health",
    "mcp",
    "record_audio",
    "transcribe_file",
    "transcribe_url",
]
