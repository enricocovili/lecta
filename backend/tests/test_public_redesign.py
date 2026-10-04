"""Public site additions: byline, page ranges/sizes."""

from __future__ import annotations

import fitz


def _pdf(pages: int, text: str) -> bytes:
    doc = fitz.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), f"{text} {i + 1}")
    data = doc.tobytes()
    doc.close()
    return data


async def _fake_publish(admin, name: str, pages: int = 5) -> dict:  # noqa: ANN001
    """Create a course and attach a publication snapshot directly (no LaTeX build needed)."""
    from app.db import SessionLocal
    from app.models import Course, Publication
    from app.services import blobs

    c = (await admin.post("/api/courses", json={"name": name, "chapters": ["Uno", "Due"]})).json()
    full = _pdf(pages, name)
    part = _pdf(2, f"{name} parte")
    async with SessionLocal() as db:
        pub = Publication(
            course_id=c["id"],
            title=name,
            description=None,
            pdf_blob=blobs.put_bytes(full),
            pdf_size=len(full),
            chapters=[
                {"slug": "uno", "title": "Uno", "position": 1, "page_start": 1, "page_end": 2,
                 "pdf_blob": blobs.put_bytes(part), "pdf_size": len(part)},
                {"slug": "due", "title": "Due", "position": 2, "page_start": 3, "page_end": pages,
                 "pdf_blob": None, "pdf_size": None},
            ],
        )
        db.add(pub)
        await db.flush()
        course = await db.get(Course, c["id"])
        course.current_publication_id = pub.id
        course.published = True
        await db.commit()
    return {**c, "pdf": full}


async def test_site_byline(admin, anon):
    r = await admin.put("/api/settings/site", json={"byline": "Enrico Covili · Unimore"})
    assert r.status_code == 200, r.text
    site = (await anon.get("/api/public/site")).json()
    assert site["byline"] == "Enrico Covili · Unimore"
    await admin.put("/api/settings/site", json={"byline": ""})


async def test_public_course_pages_and_sizes(admin, anon):
    c = await _fake_publish(admin, "Pagine Pubbliche", pages=7)
    listing = (await anon.get("/api/public/courses")).json()
    item = next(x for x in listing if x["slug"] == c["slug"])
    assert item["page_count"] == 7
    detail = (await anon.get(f"/api/public/courses/{c['slug']}")).json()
    assert detail["page_count"] == 7
    uno, due = detail["chapters"]
    assert (uno["page_start"], uno["page_end"]) == (1, 2) and uno["pdf_size"] > 0 and uno["pdf_url"]
    assert (due["page_start"], due["page_end"]) == (3, 7) and due["pdf_size"] is None and due["pdf_url"] is None
    # No internal blob hashes leak into the public payload.
    assert "pdf_blob" not in str(detail)


async def test_no_all_courses_zip(anon):
    """«Scarica tutto (ZIP)» is gone from the public site, and so is its endpoint."""
    assert (await anon.get("/api/public/courses.zip")).status_code == 404
