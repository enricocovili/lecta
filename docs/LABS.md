# Laboratories: the files explained in class

A lesson can have a **laboratory** (`labs`, one per lesson, deleted with it): the files the lecturer explains in the lab
(sources of any language, notebooks, PDFs, pictures), read in Lecta and commented while the lecture goes on. The lab is a
section of its lesson, not of the course text: nothing of it goes into the chapters (DECISIONS 103). Its chapter, when one is
needed, is the lesson's (`lessons.chapter_id`).

## Files

`lab_files` keeps one row per file, named by its **path** in the lab (`src/main.c`: folders are part of the name, unique in
the lab). A file is up to **20 MB** and a lab holds at most 500 files; uploading a name that exists replaces that file (its
`version` goes up). Names are cleaned (`services/labs.clean_path`): forward slashes, no absolute paths, no `..`, no control
or Windows-reserved characters, at most 300 characters.

What a file **is** comes from its bytes first, then its name (`services/labs.classify`):

| kind | when | stored |
|------|------|--------|
| `pdf` | starts with `%PDF-` | blob store |
| `image` | PNG, JPEG, GIF or WebP by their magic bytes (a `.png` name alone is not enough) | blob store |
| `binary` | any other recognised format (zip, archives, HEIC…) or a NUL byte in the first 8 KB (executables, `.o`, `.class`) | blob store |
| `notebook` | a `.ipynb` that parses as JSON with `cells`; `language` from its kernel | `content` |
| `text` | everything else: UTF-8 (BOM dropped), else Windows-1252 / Latin-1; CRLF becomes LF | `content` |

`language` (`c`, `python`, `sql`, `makefile`…, `text` when unknown) comes from the extension or the whole name
(`Makefile`, `Dockerfile`, `CMakeLists.txt`). Text and notebooks live in the database so they can be edited and read by the
assistant.

**Nothing is ever executed** (DECISIONS 104): there is no runner, and the browser never renders a file. Every download is
`Content-Disposition: attachment` with `nosniff` and `Content-Security-Policy: sandbox`; text goes out as
`application/octet-stream` (an uploaded `.html` or `.svg` is source to read, never a page), and only PDFs and pictures
recognised by their bytes keep their own type, so the viewer can draw them.

## The page

`/admin/courses/{course}/lessons/{n}/lab` (the lesson's address plus `/lab`); the lesson's top bar has «Laboratorio», the
lab's has «Lezione teorica». A lesson without a lab offers «Crea il laboratorio» there. On the left the **tree** of the
files (folders from the names, folders first), on the right the open file, remembered in the address (`?file=src/main.c`):

* text and notebooks in a read-only **CodeMirror** view (`components/labs/CodeView.tsx`): line numbers, folding, the
  colours of the language found from the file name (`@codemirror/language-data`, each language loaded on demand; a few
  lab extensions such as `.h`, `.m`, `.v`, `.asm` are mapped by hand), colours from the `--syn-*` tokens for both themes;
* pictures as pictures; PDFs and other files with a download link.

**Uploading**: «Carica file» (several at once), the folder button (a whole folder with its sub-folders: the names keep the
folders) or a **drop** of files and folders anywhere on the page. Files over 20 MB are refused before sending; hidden files
and `__pycache__`, `node_modules`, `__MACOSX` are left out of a folder; a file with a name that exists replaces it (a
notice says so). Each file has «Rinomina» (also into a folder: `src/main.c`) and «Elimina». The top bar shows
«Pubblicazione: in sviluppo»: labs are private for now (#59).

## Comments, written live

`lab_comments`: Markdown with `$…$` maths (KaTeX), on a file. A comment sits on **lines** (anchor `{from, to, text}`,
1-based, with the text of those lines so it can find them again after an edit; `gone` once they were removed) or on the **whole file** (`{}`). Its id is a
UUID made by the page (`crypto.randomUUID`, or `getRandomValues` on a plain-HTTP page): `PUT` creates or replaces it, so a
comment written offline and sent twice is never doubled. A comment stays on its file and goes with it.

On the page: select lines of the code and press the **«Commenta»** bubble over the selection (or Ctrl+Alt+M); «Sul file»
comments the whole file. The comments of the open file are listed on the right, whole-file ones first, then by line; the
commented lines are tinted in the code with a dot in the gutter (the number of comments when several start there), and the
dot, the card and the code point at each other. A card is written in the notes field of the lessons (lists go on with
Enter, Ctrl+B / Ctrl+I) and shown rendered when it loses the focus (Escape); an empty comment that loses the focus goes away.
The tree shows how many comments each file has.

Saving works as in the lessons (`components/labs/useLabStore.ts`): edits apply at once, are sent **once a minute, on
Ctrl+S** and when the tab is hidden; what is unsaved is mirrored in `localStorage` (`lecta:lab:<id>:unsaved`, also deletes)
and retried with a growing delay, and comes back on the next visit if the server has not changed that comment meanwhile.
The save state is the same icon as in the lesson editor. A comment is sent once it says something.

## Editing a file

A **text** file has «Modifica»: the view becomes an editor (CodeMirror with its history, Tab indents, Ctrl+Alt+M still
comments the selection) and «Fine» goes back to reading. The text is saved in the same queue as the comments (once a minute,
Ctrl+S, «Fine», the tab hidden; unsent edits come back after a reload if the file has not changed meanwhile) with the version
it started from: if the file changed elsewhere meanwhile (`409`), the edit is dropped with a notice and the file is read
again. Notebooks are not edited by hand.

**Comments follow their lines.** In the editor every edit maps each comment's lines through the change (CodeMirror position
mapping): lines added above move it, lines added inside widen it, its text is kept up to date. A comment whose lines are all
deleted stays on the file as **«Righe rimosse»** (`anchor.gone`, with the text it had), at the end of the list and no longer
marked in the code. When a text file is **uploaded again** under the same name, or later changed by the assistant, the
server does the same with a line diff (`services/labs.remap_anchors`: the unchanged lines carry the comment; none left →
`gone`), and the page reads the file and its comments again.

## Notes

Besides the comments a lab has free **notes** (`labs.notes`, Markdown, with its own version): announcements, deadlines,
what the exam asks, anything not tied to a file. «Note del laboratorio» at the top of the tree opens them (`?notes` in the
address), with the same notes field and a preview; they are saved with the comments, in the same queue
(`components/labs/useLabStore.ts`).

## API (admin only, like the rest)

The routes hang off the lesson, so a share link of the lesson can reach the same lab.

| call | |
|------|-|
| `GET /api/lessons/{id}/lab` · `GET /api/courses/{course_id}/lessons/{n}/lab` | the lab: `notes`, `notes_version`, `lesson` (id, number, title, course, chapter), `files` (no content) and `comments` · 404 when the lesson has none |
| `POST /api/lessons/{id}/lab` | makes the lab if the lesson has none, else returns it |
| `DELETE /api/lessons/{id}/lab` | the lab and its files |
| `PUT /api/lessons/{id}/lab/notes` `{notes}` | the lab's free notes (replace) → `{version}` |
| `POST /api/lessons/{id}/lab/files?path=` | raw body: one file (the page sends several in a row); same path = replace (the comments of a text file follow their lines) → the file with `replaced` |
| `GET /api/lessons/{id}/lab/files/{fid}` | the file with `content` (text and notebooks; `null` for the others) |
| `PUT /api/lessons/{id}/lab/files/{fid}/content` `{content, base_version}` | the text of a text file, edited by hand → the file; `409` with the current `version` if it changed since `base_version`, or if it isn't text |
| `PATCH /api/lessons/{id}/lab/files/{fid}` `{path}` · `DELETE` | rename (a text file takes the new name's language; a taken name is 409) · remove |
| `GET /api/lessons/{id}/lab/files/{fid}/raw` | the file to download (text in UTF-8), always as an attachment |
| `PUT /api/lessons/{id}/lab/comments/{uuid}` `{file_id, anchor, body}` | create or replace a comment → the comment with its `version`; another file or lab's comment is 404 |
| `DELETE /api/lessons/{id}/lab/comments/{uuid}` | remove it (already gone is fine) |

`GET /api/lessons/{id}` tells whether the lesson has a lab: `lab: {files, comments}` (counts) or `null`.
