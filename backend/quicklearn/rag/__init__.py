"""Retrieval-augmented generation: indexing, hybrid retrieval, citation resolution.

Only submodules are exported. Re-exporting the ``retrieve`` *function* here would shadow the
``retrieve`` *module*, so ``from ..rag import retrieve`` would silently hand callers a function
where they expected a module — a failure that only shows up at the first attribute access.
"""

from . import index, retrieve

__all__ = ["index", "retrieve"]
