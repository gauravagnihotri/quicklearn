"""Job handlers.

Importing this package registers every handler with the worker pool. The worker imports it
lazily at startup so the import graph stays acyclic — handlers depend on the pool for the
``@register`` decorator, and the pool must not depend on them at module scope.
"""

from __future__ import annotations

from . import ingest, podcast, study

__all__ = ["ingest", "podcast", "study"]
