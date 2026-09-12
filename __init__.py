"""LarkDeck — streaming Feishu / Lark cards for Hermes Agent.

The plugin entry point is :func:`register`, called by the Hermes plugin system.
See ``adapter.py`` for the architecture note.
"""

from .adapter import register

__all__ = ["register"]
