"""Atlas HTTP API — composition root for Companion and other clients."""

from .app import create_app

__all__ = ["create_app"]
