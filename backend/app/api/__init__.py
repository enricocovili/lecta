"""API routers. `extra_routers` lists the routers added after milestone 1."""

from __future__ import annotations

from fastapi import APIRouter


def extra_routers() -> list[APIRouter]:
    from . import chat, concepts, costs, editor, invites, labs, lessons, notes, passkeys, preview, providers, publish, search, settings, shared, uploads, users

    return [
        editor.router, publish.router, publish.admin_router, settings.router, providers.router, costs.router,
        uploads.router, chat.router, search.router,
        concepts.router, preview.router, lessons.router, lessons.shares_router, labs.router, shared.router, passkeys.router, settings.template_router,
        invites.admin_router, invites.router, users.router, notes.router,
    ]
