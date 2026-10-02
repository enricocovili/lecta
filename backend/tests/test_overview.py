"""Workspace overview: course states, activity feed, nav counts, tree status."""

from __future__ import annotations

from app.models import Build, Job


async def _course(admin, name: str) -> dict:
    r = await admin.post("/api/courses", json={"name": name, "chapters": ["Uno", "Due"]})
    assert r.status_code == 201, r.text
    return r.json()


async def test_course_states_and_activity(admin, db):
    ok = await _course(admin, "Corso Sano")
    broken = await _course(admin, "Corso Rotto")
    busy = await _course(admin, "Corso Occupato")
    db.add(Build(course_id=ok["id"], kind="draft", status="ok", engine="pdflatex"))
    db.add(
        Build(
            course_id=broken["id"], kind="draft", status="error", engine="pdflatex",
            diagnostics=[{"level": "error", "file": broken["chapters"][1]["path"], "line": 12, "message": "Undefined control sequence."}],
        )
    )
    db.add(Job(kind="ingest", title="Lezione 6", status="running", progress=0.5, course_id=busy["id"]))
    db.add(Job(kind="index.rebuild", title="hidden", status="running", course_id=busy["id"]))
    db.add(Job(kind="publish", title="Pubblicazione", status="succeeded", course_id=ok["id"]))
    await db.commit()

    d = (await admin.get("/api/dashboard")).json()
    states = {c["id"]: c for c in d["courses"]}
    assert states[ok["id"]]["status"] == "ok"
    assert states[broken["id"]]["status"] == "error" and states[broken["id"]]["reason"] == "compile"
    fe = states[broken["id"]]["last_build"]["first_error"]
    assert fe["line"] == 12 and fe["chapter_title"] == "Due"
    assert states[busy["id"]]["status"] == "working"
    assert states[busy["id"]]["active_job"]["title"] == "Lezione 6"
    kinds = [a["kind"] for a in d["activity"]]
    assert "index.rebuild" not in kinds and {"ingest", "publish"} <= set(kinds)

    tree = {c["id"]: c for c in (await admin.get("/api/tree")).json()}
    assert tree[broken["id"]]["status"] == "error" and tree[busy["id"]]["status"] == "working"
    assert "sources" in tree[ok["id"]]

    counts = (await admin.get("/api/dashboard/counts")).json()
    assert set(counts) == {"inbox_count", "jobs_active"}
    assert counts["jobs_active"] >= 2


async def test_failed_job_after_build_marks_error(admin, db):
    c = await _course(admin, "Corso Fallito")
    db.add(Build(course_id=c["id"], kind="draft", status="ok", engine="pdflatex"))
    await db.commit()
    db.add(Job(kind="ingest", title="Import", status="failed", error="boom\ndetails", course_id=c["id"]))
    await db.commit()
    d = (await admin.get("/api/dashboard")).json()
    st = next(x for x in d["courses"] if x["id"] == c["id"])
    assert st["status"] == "error" and st["reason"] == "job" and st["failed_job"]["error"] == "boom"
