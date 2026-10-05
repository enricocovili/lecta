# Lessons: taking notes during the lecture

The lesson workspace (`/admin/lessons`, editor at `/admin/courses/{course}/lessons/{n}` where `n` counts the lessons of that course: 1, 2, 3…; the old `/admin/lessons/{id}` redirects) is where the notes of a lecture are taken,
and the input of the import: what is written there becomes the study text of the subject.

A **lesson** belongs to a course and has **pages**: the slides of an uploaded PDF (one page per slide) plus blank
pages added by hand (a slide never has enough room). Every page has:

* **Markdown notes**, next to the slide (or below it): a plain text field that continues lists on Enter, indents with
  Tab, wraps `**bold**` / `*italic*` with Ctrl+B / Ctrl+I, grows with what is written, and has a rendered preview
  (maths with KaTeX). There is no limit to the room: the row grows past the slide.
* **Ink over the slide**: pen (3 widths, 6 colours), highlighter, stroke eraser (drawn as a ring that follows the pointer, also while erasing; also the stylus' eraser end), a "hand" tool
  that only scrolls, undo/redo (Ctrl+Z, Ctrl+Shift+Z): **one history** in the order things were done — strokes, erasing, the typed notes (typing in bursts, one step per pause or paste, kept as small patches in `components/lessons/history.ts`, also while the cursor is in a note) and removed pages (a removed slide asks in a dialog, and comes back with its notes and strokes through `pages/restore`). Mouse, stylus and finger; by default a finger scrolls the page and
  only mouse and pen write ("Dito scrive" makes the finger write), and touches right after a pen was seen are ignored
  (palm rejection). Pressure changes the width of pen strokes. **Shape recognition** (toggle «Forme», on by default), as in Xournal++: when a stroke is let go and it is a straight line (an underline; a line within 3° of level or plumb is made exactly so), a rectangle (also tilted), a triangle or an ellipse (a circle when the axes differ by under 12 %), it is replaced by the clean shape (`components/lessons/shapes.ts`, tested with `npm test`). Anything small (under 5 % of the page width) is handwriting and is left alone, like scribbles and open arcs; undo takes the whole shape away.

The screen is kept on (Wake Lock) while the editor is open, and the layout is chosen by the user: slide + notes side by
side (automatic single column under ~860 px), notes below the slide, or slides only. Zoom, a page rail with dots for
"has notes" / "has ink", previous/next page (buttons, or the ← / → keys, also in a shared lesson; not while typing or in a dialog).

Everything is **saved once a minute and on Ctrl+S** (which replaces the browser's "save page", also while typing in a note; the
state next to the title is one icon (da salvare, in salvataggio, salvata, or a warning while it keeps retrying), with its
words in the tooltip, and can be clicked to save; «Scarica» is a download icon too), and at once when the tab is hidden or a
page is added. Edits are mirrored in `localStorage` as they are made. Work that could not be sent stays in memory and in `localStorage`, is retried with a growing delay
and comes back on the next visit if the server has not changed that page meanwhile.

## State: in corso / completata

Every lesson has a `status`: **working** («In corso») while it is being written, **completed** («Completata») once its
text is merged into the subject's notes. A generation that writes into a chapter (`new_chapter`, `append`)
sets it. It then stays completed, also when the lesson is edited again:
only the user moves it back. The owner sees a «Completata» switch (on = completed) in the list and in the editor's top
bar, and can turn it on or off at any time without a generation (`PATCH … {status}`); the shared read-only view shows
the state as a pill.

## Ink format

Strokes are stored the way the browser draws them, in **units of the page's width** (x and y both, so any zoom or screen
fits, and the server can draw the same thing on the PDF):

```json
[{"t": "pen", "c": "#d32f2f", "w": 0.0032, "p": [0.1, 0.1, 0.5,  0.5, 0.1, 0.6,  0.5, 0.3, 0.5]}]
```

`t` is `pen` or `hl` (highlighter, 35 % opacity, drawn under the pens), `c` `#rrggbb`, `w` the stroke width, `p` triples
`x, y, pressure`. The server validates everything (`services/lessons.clean_ink`): at most 4000 strokes and 250 000
points per page, sane coordinates, colour and width.

## API (admin only, like the rest)

| call | |
|------|-|
| `GET /api/lessons?course_id=` | list with `page_count`, `notes_pages`, `ink_pages`, `generated_at` |
| `POST /api/lessons` `{course_id, title}` | new lesson with one blank page |
| `POST /api/lessons/{id}/slides?name=` | raw PDF body: adds the slides (only once; untouched blank pages are replaced) |
| `GET /api/courses/{course_id}/lessons/{n}` · `GET /api/lessons/{id}?pages=false` | the lesson (+ pages with notes and ink), `course_guidelines` |
| `PATCH /api/lessons/{id}` `{title?, course_id?, status?}` · `DELETE` | `status`: `working` \| `completed` |
| `PUT /api/lessons/{id}/last-page` `{page_id}` | the page being looked at (the editor sends it a moment after the page settles); opening the lesson scrolls there |
| `PUT /api/lessons/{id}/pages/{pid}` `{notes?, ink?}` | what is sent replaces what is stored, the rest is untouched → `{version}` |
| `POST /api/lessons/{id}/pages` `{after_page_id?}` · `DELETE …/pages/{pid}` | add a blank page · remove any page, slides included (the last one stays); a removed slide leaves the lesson but not the stored PDF |
| `POST /api/lessons/{id}/pages/restore` `{id, kind, slide_page, ratio, position, notes, ink}` | undo of a removal: the page comes back **with its own id** (only an id that was already handed out, and not taken), notes and strokes, at its place |
| `GET /api/lessons/{id}/pdf` | the slides |
| `GET /api/lessons/{id}/annotated.pdf` | slides and added pages in order with the strokes drawn on them |
| `GET /api/lessons/{id}/notes.md` | the typed notes as the import receives them |
| `POST /api/lessons/{id}/generate` `{guidelines?, save_guidelines, chapter_id?, placement?}` | starts the import → `{job_id}`; `placement`: `update` (default when the lesson has a chapter), `new_chapter` (default otherwise), `auto` |

## Sharing a lesson

A lesson can be opened by someone who is not signed in, through a link `/s/<token>` (a random 192-bit secret, stored in `lesson_shares`;
one link per mode and lesson). **Sola lettura**: slides, notes (rendered Markdown) and strokes, refreshed every few seconds so a
classmate can follow the lecture; downloads of the annotated PDF and the notes. **Può modificare**: the same editor as the owner's
(notes, pen, highlighter, eraser, undo, added and removed pages) without what is the owner's alone (generating the text, the title,
sharing, moving or deleting the lesson). Edits of different people are merged page by page (the polling brings in the pages others saved;
a page one is editing is not replaced until it has been saved, and saving replaces that page's notes or strokes: the last save wins).
The owner manages the links in «Condividi»: create, copy, replace (the old link stops working) or revoke. A link dies with its lesson.

| call | |
|------|-|
| `GET/POST /api/lessons/{id}/shares` `{mode: read\|write, new_link?}` · `DELETE /api/lessons/{id}/shares/{mode}` | the owner's links |
| `POST /api/lessons/{id}/sync` `{known: {page_id: version}}` | the order of the pages and the pages whose version differs (the owner's editor uses it while the lesson has links) |
| `GET /api/public/lesson/{token}` · `/pdf` · `/annotated.pdf` · `/notes.md` | what a link shows (no guidelines, chapter or ids of the owner's side) |
| `POST …/sync` · `PUT …/pages/{pid}` · `POST …/pages` · `POST …/pages/restore` · `DELETE …/pages/{pid}` | the same as the owner's, **write links only** (403 on a read link) |

## From the lesson to the text

`generate` takes a **snapshot** of the lesson (pages, notes, ink) and enqueues the normal `ingest` job with the payload
`{upload_id, lesson_id, lesson_snapshot, guidelines}` and an `Upload` with `via = "lesson"` targeted at the lesson's course
(or chapter). The job's first step (`pipeline/lesson.py`) makes the upload's files from the snapshot:

| what | becomes |
|------|---------|
| the slides PDF | the PDF file; slides that carry ink are replaced by a **picture of the slide with the strokes drawn on it** and routed to the vision step (`why: handwriting of the student`), so the model sees what was underlined, circled or written in the margin |
| the typed notes | one Markdown file `# Title` + a `## Slide N` section per slide (`## Pagina aggiunta dopo la slide N` for added pages): the **backbone** of the text, in the order of the lecture |
| a blank page with ink | a picture read like a photo of handwritten notes |

Then the usual pipeline runs: everything of one lesson is **one group** (one chapter), reading, writing, placement (**the first time a new chapter: one lesson, one chapter**;
`chapter_id` puts the text in that chapter, `placement: "auto"` lets the usual placement compare with the existing chapters), compile check. The bond between notes and slides is kept end to end: the reading step marks every
slide with a `% slide N` comment, the notes have `Slide N` headings, and the writing prompt is told to pair them and to treat the
student's handwriting as notes; neither the numbers nor the markers appear in the text. Slides and notes overlap (the notes copy or rephrase the slide): the writing prompt (`notes.compose`) tells the model to merge them, stating each fact once in the notes' order with the slide's exact formulas and names, and never to repeat a recap slide or a picture that comes from both. When the job ends the lesson
remembers `generated_at` and `last_result` (chapters written).

## Lesson ↔ chapter: changing a lesson that is already in the course

The link is `lessons.chapter_id` (set when a generation ends; cleared if the chapter is deleted or the lesson moves to another
subject). In the chapter's `.tex` the lesson's text sits between two marker comments:

```latex
% ==== Lecta: lezione 12 «Campionamento» (30/09/2026)
…the text…
% ==== Lecta: fine lezione 12
```

Generating a lesson that has a chapter (the dialog's default, «Aggiorna "NN titolo"»; `placement: "update"`) **integrates** the change
instead of adding a chapter or repeating the text: the lesson's section is read from the chapter, handed to the writing step as the
PREVIOUS VERSION (`notes.compose`: keep structure, wording, formulas, labels and `images/…` pictures wherever the notes still support
them, add what is new, remove only what the notes no longer contain, keep the student's additions), and the new text replaces that
section **in place**. Everything outside the markers (other lessons, what was written by hand before or after) is not touched. A
chapter of its own for the same lesson is «Un capitolo nuovo» (`placement: "new_chapter"`); choosing another chapter moves the link
there (the section is added, or replaced if the lesson already had one in it). Chapters written before this link existed have no markers,
so their lesson gets a new chapter the first time.

## Writing guidelines

Each course has `guidelines` (text, up to 8000 characters; empty = the AI decides). They are asked **every time a text is
generated** from a lesson (`GenerateDialog`).
The dialog is prefilled with the saved guidelines; when the box is empty a notice says
*«Non hai inserito linee guida: genererò il testo in automatico…»* and the button reads «Integra appunti» («Integra con queste linee guida» with some). A checkbox keeps
the text as the course's guidelines (also editable in the course settings). They reach the writing step as an instruction
(`compose.GUIDELINES_INTRO`) and the assistant's system prompt, and never override the reply format or the rules about
formulas, pictures and not inventing content. An import started without saying anything about guidelines falls back to the
course's saved ones.
