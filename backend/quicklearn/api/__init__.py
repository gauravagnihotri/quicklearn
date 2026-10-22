"""REST and WebSocket API, all under ``/api/v1`` (PRD §8).

No authentication: the server binds to ``127.0.0.1`` and there is exactly one user. Adding auth
would mean inventing an account system for a single-user localhost app, which PRD §1.4 rules
out explicitly.
"""

from __future__ import annotations

from fastapi import APIRouter

from . import cards, chat, learn, library, notes, podcasts, quiz, sources, system

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(sources.router)
api_router.include_router(notes.router)
api_router.include_router(cards.router)
api_router.include_router(quiz.router)
# Local patch: the Learn tab.
api_router.include_router(learn.router)
api_router.include_router(chat.router)
api_router.include_router(podcasts.router)
api_router.include_router(library.router)
api_router.include_router(system.router)

__all__ = ["api_router"]
