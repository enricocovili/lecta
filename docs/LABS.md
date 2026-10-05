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

## API (admin only, like the rest)

The routes hang off the lesson, so a share link of the lesson can reach the same lab.

| call | |
|------|-|
| `GET /api/lessons/{id}/lab` · `GET /api/courses/{course_id}/lessons/{n}/lab` | the lab: `lesson` (id, number, title, course, chapter) and `files` (no content) · 404 when the lesson has none |
| `POST /api/lessons/{id}/lab` | makes the lab if the lesson has none, else returns it |
| `DELETE /api/lessons/{id}/lab` | the lab and its files |
| `POST /api/lessons/{id}/lab/files?path=` | raw body: one file (the page sends several in a row); same path = replace → the file with `replaced` |
| `GET /api/lessons/{id}/lab/files/{fid}` | the file with `content` (text and notebooks; `null` for the others) |
| `PATCH /api/lessons/{id}/lab/files/{fid}` `{path}` · `DELETE` | rename (a text file takes the new name's language; a taken name is 409) · remove |
| `GET /api/lessons/{id}/lab/files/{fid}/raw` | the file to download (text in UTF-8), always as an attachment |

`GET /api/lessons/{id}` tells whether the lesson has a lab: `lab: {files}` or `null`.
