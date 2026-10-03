from __future__ import annotations

import asyncio

from sqlalchemy import select, update

from app.db import SessionLocal
from app.models import Job, JobStep

from .conftest import wait_job


async def test_job_runs_with_progress_and_logs(admin, worker):
    r = await admin.post("/api/diag/jobs/demo", json={"steps": 3, "delay": 0.05})
    job_id = r.json()["id"]
    j = await wait_job(admin, job_id)
    assert j["status"] == "succeeded", j
    assert j["progress"] == 1.0
    assert j["result"] == {"steps": 3}
    assert any("slept 3 steps" in log["message"] for log in j["logs"])


async def test_failed_job_can_be_retried_and_resumes(admin, worker):
    r = await admin.post("/api/diag/jobs/demo", json={"steps": 2, "delay": 0.01, "fail": True})
    job_id = r.json()["id"]
    j = await wait_job(admin, job_id)
    assert j["status"] == "failed" and "requested failure" in j["error"]
    async with SessionLocal() as db:
        steps = (await db.execute(select(JobStep.key).where(JobStep.job_id == job_id))).scalars().all()
        assert sorted(steps) == ["sleep-0", "sleep-1"]
        # Make the retry succeed.
        await db.execute(update(Job).where(Job.id == job_id).values(payload={"steps": 2, "delay": 0.01}))
        await db.commit()
    assert (await admin.post(f"/api/jobs/{job_id}/retry", json={})).status_code == 200
    j = await wait_job(admin, job_id)
    assert j["status"] == "succeeded"
    assert j["attempts"] == 2


async def test_cancel_running_job(admin, worker):
    r = await admin.post("/api/diag/jobs/demo", json={"steps": 50, "delay": 0.2})
    job_id = r.json()["id"]
    await wait_job(admin, job_id, until=("running",))
    await asyncio.sleep(0.3)
    assert (await admin.post(f"/api/jobs/{job_id}/cancel")).status_code == 200
    j = await wait_job(admin, job_id, timeout=30)
    assert j["status"] == "cancelled"


async def test_job_survives_worker_restart(admin):
    """A job left 'running' by a dead worker is re-queued and completed by the next one."""
    from app.worker.runner import Worker

    r = await admin.post("/api/diag/jobs/demo", json={"steps": 2, "delay": 0.01})
    job_id = r.json()["id"]
    async with SessionLocal() as db:
        await db.execute(update(Job).where(Job.id == job_id).values(status="running"))
        await db.commit()
    w = Worker()
    task = asyncio.create_task(w.run())
    try:
        j = await wait_job(admin, job_id, timeout=30)
        assert j["status"] == "succeeded"
        assert any("recovered after worker restart" in log["message"] for log in j["logs"])
    finally:
        w.stop()
        await asyncio.wait_for(task, timeout=10)


async def test_job_events_sse(admin, worker):
    r = await admin.post("/api/diag/jobs/demo", json={"steps": 2, "delay": 0.05})
    job_id = r.json()["id"]
    events = []
    async with admin.stream("GET", f"/api/jobs/{job_id}/events") as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        async for line in resp.aiter_lines():
            if line.startswith("event:"):
                events.append(line.split(":", 1)[1].strip())
            if events and events[-1] == "end":
                break
    assert "job" in events and "end" in events


async def test_delete_finished_job(admin, worker):
    r = await admin.post("/api/diag/jobs/demo", json={"steps": 1, "delay": 0.01, "fail": True})
    job_id = r.json()["id"]
    assert (await wait_job(admin, job_id))["status"] == "failed"
    r = await admin.delete(f"/api/jobs/{job_id}")
    assert r.status_code == 200 and r.json()["deleted"] == [job_id]
    assert (await admin.get(f"/api/jobs/{job_id}")).status_code == 404
    async with SessionLocal() as db:
        assert not (await db.execute(select(JobStep).where(JobStep.job_id == job_id))).first()


async def test_active_job_cannot_be_deleted(admin):
    r = await admin.post("/api/diag/jobs/demo", json={"steps": 1, "delay": 0.01})  # no worker: stays queued
    job_id = r.json()["id"]
    assert (await admin.delete(f"/api/jobs/{job_id}")).status_code == 409
    await admin.post(f"/api/jobs/{job_id}/cancel")
    assert (await admin.delete(f"/api/jobs/{job_id}")).status_code == 200


async def test_bulk_delete_with_purge_removes_test_outputs(admin):
    from app.models import Course, IngestItem, SourceFile, Upload

    async with SessionLocal() as db:
        course = Course(name="Purge test", slug="purge-test")
        db.add(course)
        await db.flush()
        jobs = [Job(kind="ingest", title=f"test {n}", status="cancelled") for n in range(2)]
        db.add_all(jobs)
        await db.flush()
        up = Upload(status="failed", job_id=jobs[0].id)
        db.add(up)
        await db.flush()
        sf = SourceFile(upload_id=up.id, name="x.pdf", kind="pdf")
        db.add(sf)
        await db.flush()
        db.add(IngestItem(job_id=jobs[0].id, upload_id=up.id, source_file_id=sf.id, key="i1", kind="slide", label="x.pdf · p. 1", position=1))
        await db.commit()
        ids = [j.id for j in jobs]
        up_id = up.id
    r = await admin.post("/api/jobs/delete", json={"status": "cancelled", "purge": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(ids) <= set(body["deleted"]) and body["uploads"] == 1
    async with SessionLocal() as db:
        assert await db.get(Upload, up_id) is None
        assert not (await db.execute(select(IngestItem).where(IngestItem.job_id == ids[0]))).first()
