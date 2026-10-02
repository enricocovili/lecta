"""Diagnostics used by the smoke tests to verify the frontend proxy end to end:
SSE streaming, WebSocket upgrade and large streaming uploads (all private)."""

from __future__ import annotations

import asyncio
import hashlib

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..security.auth import require_admin
from ..services import jobs as jobs_svc

router = APIRouter(prefix="/diag")


@router.get("/sse", dependencies=[Depends(require_admin)])
async def sse(n: int = 3) -> StreamingResponse:
    async def gen():
        for i in range(min(n, 20)):
            yield f"event: tick\ndata: {i}\n\n"
            await asyncio.sleep(0.3)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})


@router.post("/upload", dependencies=[Depends(require_admin)])
async def upload(request: Request) -> dict:
    """Consume the body as a stream (never buffered) and report size + sha256."""
    h = hashlib.sha256()
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        h.update(chunk)
    return {"size": size, "sha256": h.hexdigest()}


@router.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    # The ASGI guard already rejected anonymous connections before we get here.
    await websocket.accept()
    try:
        while True:
            msg = await websocket.receive_text()
            await websocket.send_text(f"echo:{msg}")
    except WebSocketDisconnect:
        return


class DemoJobIn(BaseModel):
    steps: int = Field(3, ge=1, le=100)
    delay: float = Field(0.2, ge=0, le=10)
    fail: bool = False


@router.post("/jobs/demo", dependencies=[Depends(require_admin)])
async def demo_job(body: DemoJobIn, db: AsyncSession = Depends(get_db)) -> dict:
    job = await jobs_svc.enqueue(db, "demo.sleep", body.model_dump(), title="Demo job", priority="maintenance")
    return {"id": job.id}
