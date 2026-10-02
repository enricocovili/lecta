"""Hybrid retrieval + placement, with embeddings on and with embeddings off (lexical fallback)."""

from __future__ import annotations

import pytest
from sqlalchemy import select, text

from app.db import SessionLocal
from app.models import IndexChunk, Job
from app.pipeline import indexer, placement
from app.services import retrieval, semantic
from app.services import settings as settings_svc
from app.worker.context import JobContext

from .test_ingest_e2e import setup_fake

LTI = r"""\chapter{Sistemi LTI}
\section{Risposta all'impulso}
Un sistema lineare tempo-invariante è caratterizzato completamente dalla sua risposta all'impulso $h(t)$.
L'uscita si ottiene come convoluzione tra l'ingresso e la risposta all'impulso: $y = h * x$.
\section{Stabilità}
Il sistema è stabile BIBO se la risposta all'impulso è assolutamente integrabile. La causalità richiede $h(t)=0$ per $t<0$.
"""
FOURIER = r"""\chapter{Trasformata di Fourier}
\section{Spettro}
La trasformata di Fourier rappresenta un segnale nel dominio della frequenza; lo spettro di ampiezza e di fase
descrive il contenuto armonico. La trasformata di un rettangolo è una funzione sinc.
"""
THERMO = r"""\chapter{Termodinamica}
\section{Entropia}
L'entropia di un sistema isolato non diminuisce mai. Il calore scambiato diviso per la temperatura assoluta
definisce la variazione di entropia in una trasformazione reversibile. Il ciclo di Carnot ha il rendimento massimo.
"""
Q_CONV = "Convoluzione e risposta all'impulso: calcolo dell'uscita di un sistema LTI causale e stabile tramite l'integrale di convoluzione."
Q_ENTROPY = "Secondo principio: l'entropia aumenta nei processi irreversibili; calore, temperatura e rendimento delle macchine termiche."
Q_ART = "Il Rinascimento fiorentino: Brunelleschi, la cupola del Duomo, la prospettiva lineare e la pittura di Masaccio."
Q_EN = "Impulse response and convolution: the output of a linear time-invariant system is the input convolved with h."


async def _library(admin):
    """A fresh library (placement looks at every course, so start from none)."""
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM courses"))
        await db.commit()
    ids = {}
    c1 = (await admin.post("/api/courses", json={"name": "Teoria dei Segnali", "language": "it", "chapters": ["Sistemi LTI", "Trasformata di Fourier"]})).json()
    c2 = (await admin.post("/api/courses", json={"name": "Fisica Tecnica", "language": "it", "chapters": ["Termodinamica"]})).json()
    for ch, src in zip(c1["chapters"] + c2["chapters"], [LTI, FOURIER, THERMO], strict=True):
        await admin.put(f"/api/courses/{ch['course_id']}/files/content", json={"path": ch["path"], "content": src})
        ids[ch["title"]] = ch["id"]
    return ids


async def _ctx() -> JobContext:
    async with SessionLocal() as db:
        j = Job(kind="test.place", title="placement test", status="running")
        db.add(j)
        await db.commit()
        await db.refresh(j)
    return JobContext(j)


def _bundle(title: str, body: str, key: str) -> dict:
    return {"title": title, "body": body, "outline": [], "group_key": key, "sources": ["source:1"], "keys": []}


@pytest.fixture
async def embeddings_mode(request):
    """Switch embeddings on (benchmarked) or off for the test."""
    on = request.param
    async with SessionLocal() as db:
        await settings_svc.set_section(db, "embeddings", {"model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2" if on else "off",
                                                          "min_chunks_per_s": 0.1})
    semantic.ALLOW_LOAD = True
    if on:
        res = await indexer.ensure_benchmark(force=True)
        assert res["ok"] and res["chunks_per_s"] > 0 and res["dim"] == 384, res
    yield on


@pytest.mark.parametrize("embeddings_mode", [True, False], ids=["embeddings-on", "embeddings-off"], indirect=True)
async def test_placement_in_both_modes(admin, embeddings_mode):
    await setup_fake(admin)
    ids = await _library(admin)
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM index_state"))
        await db.commit()
    await indexer.index_pending(limit=100)
    async with SessionLocal() as db:
        st = await semantic.status(db)
        assert st["enabled"] is embeddings_mode, st
        embedded = (await db.execute(select(IndexChunk).where(IndexChunk.embedding.is_not(None)))).scalars().all()
        assert bool(embedded) is embeddings_mode
        cands, mode = await retrieval.candidates(db, Q_CONV, title="Convoluzione")
        assert mode == ("hybrid" if embeddings_mode else "lexical")
        assert cands[0]["chapter_id"] == ids["Sistemi LTI"], cands[:3]

    ctx = await _ctx()
    out = await placement.decide(ctx, _bundle("Convoluzione", Q_CONV, "conv"), target_course_id=None, target_chapter_id=None)
    assert out["type"] == "merge" and out["chapter_id"] == ids["Sistemi LTI"], out
    out = await placement.decide(ctx, _bundle("Entropia", Q_ENTROPY, "entr"), target_course_id=None, target_chapter_id=None)
    assert out["type"] == "merge" and out["chapter_id"] == ids["Termodinamica"], out
    out = await placement.decide(ctx, _bundle("Rinascimento", Q_ART, "art"), target_course_id=None, target_chapter_id=None)
    assert out["type"] == "inbox", out


@pytest.mark.parametrize("embeddings_mode", [True], ids=["embeddings-on"], indirect=True)
async def test_semantic_retrieval_is_cross_lingual_and_incremental(admin, embeddings_mode):
    await setup_fake(admin)
    ids = await _library(admin)
    await indexer.index_pending(limit=100)
    async with SessionLocal() as db:
        cands, mode = await retrieval.candidates(db, Q_EN, title="Impulse response")
        assert mode == "hybrid"
        assert cands[0]["chapter_id"] == ids["Sistemi LTI"], cands[:3]  # English query, Italian notes
        before = dict((await db.execute(select(IndexChunk.id, IndexChunk.content_hash).where(IndexChunk.chapter_id == ids["Sistemi LTI"]))).all())
    # Changing one section re-embeds only the chunks that changed.
    detail = (await admin.get("/api/tree")).json()
    course = next(c for c in detail if any(ch["id"] == ids["Sistemi LTI"] for ch in c["chapters"]))
    path = next(ch["path"] for ch in course["chapters"] if ch["id"] == ids["Sistemi LTI"])
    await admin.put(f"/api/courses/{course['id']}/files/content", json={"path": path, "content": LTI.replace("La causalità", "Inoltre la causalità")})
    await indexer.index_pending(limit=100)
    async with SessionLocal() as db:
        after = dict((await db.execute(select(IndexChunk.id, IndexChunk.content_hash).where(IndexChunk.chapter_id == ids["Sistemi LTI"]))).all())
    kept = set(before) & set(after)
    assert kept and len(after) == len(before) and len(kept) < len(after)


@pytest.mark.parametrize("embeddings_mode", [True], ids=["embeddings-on"], indirect=True)
async def test_slow_embeddings_fall_back_to_lexical(admin, embeddings_mode):
    async with SessionLocal() as db:
        await settings_svc.set_section(db, "embeddings", {"min_chunks_per_s": 1e9})
        st = await semantic.status(db)
        assert not st["enabled"] and "too slow" in st["reason"]
        _, mode = await retrieval.candidates(db, Q_CONV)
        assert mode == "lexical"
    r = await admin.get("/api/index/status")
    assert r.json()["mode"] == "lexical"


async def test_inbox_actions(admin, worker):
    """Unplaceable material lands in the inbox; assign / new course / discard."""
    from .test_ingest_e2e import upload

    await setup_fake(admin)
    md = ("# Il Rinascimento\n\n" + Q_ART + "\n\nLa prospettiva di Brunelleschi.\n").encode()
    job_id = await upload(admin, [("rinascimento.md", md)])
    from .conftest import wait_job

    j = await wait_job(admin, job_id, timeout=240)
    assert j["status"] == "succeeded", j["error"]
    items = (await admin.get("/api/inbox")).json()
    item = next(i for i in items if i["job_id"] == job_id)
    detail = (await admin.get(f"/api/inbox/{item['id']}")).json()
    assert detail["body"] and detail["source_items"] and "guesses" in detail
    assert (await admin.get("/api/dashboard")).json()["inbox_count"] >= 1

    r = await admin.post(f"/api/inbox/{item['id']}/new-course", json={"name": "Storia dell'Arte"})
    assert r.status_code == 200 and r.json()["language"] == "it"
    j = await wait_job(admin, r.json()["job_id"], timeout=240)
    assert j["status"] == "succeeded", j["error"]
    chapters = (await admin.get(f"/api/courses/{r.json()['course_id']}")).json()["chapters"]
    assert len(chapters) == 1 and j["result"]["groups"][0]["compile"] == "ok"
    assert (await admin.get(f"/api/inbox/{item['id']}")).json()["status"] == "assigned"

    # Another one: discard.
    cooking = "# Carbonara\n\nGuanciale croccante, pecorino romano, tuorli d'uovo e pepe nero: la ricetta tradizionale romana.\n"
    job2 = await upload(admin, [("ricetta.md", cooking.encode())])
    await wait_job(admin, job2, timeout=240)
    item2 = next(i for i in (await admin.get("/api/inbox")).json() if i["job_id"] == job2)
    assert (await admin.post(f"/api/inbox/{item2['id']}/discard")).status_code == 200
    assert all(i["id"] != item2["id"] for i in (await admin.get("/api/inbox")).json())
