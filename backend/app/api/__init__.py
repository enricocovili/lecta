"""API routers. `extra_routers` lists the routers added after milestone 1."""

from __future__ import annotations

from fastapi import APIRouter


def extra_routers() -> list[APIRouter]:
    from . import chat, concepts, costs, editor, labs, lessons, passkeys, preview, providers, publish, search, settings, shared, uploads

    return [
        editor.router, publish.router, settings.router, providers.router, costs.router,
        uploads.router, chat.router, search.router,
        concepts.router, preview.router, lessons.router, labs.router, shared.router, passkeys.router,
    ]
