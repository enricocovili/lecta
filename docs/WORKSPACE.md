# The workspace

How a course is worked on, read and published. The API of the draft and the assistant is in
[AI-WORKSPACE.md](AI-WORKSPACE.md); lessons and labs in [LESSONS.md](LESSONS.md) and [LABS.md](LABS.md).

## A course

**A course** opens on its page (`/admin/courses/{id}`): its **lessons** (with the «Completata» switch and «Nuova
lezione»), the **chapters** of its text (one click opens the text at that chapter), publishing ON/OFF, the PDF and LaTeX
downloads and the course settings. **Apri il testo** leads into the text. A **Laboratori** section lists the labs of its
lessons (files and comments), each a click from its lab and its lesson.

The text of a course opens at `/admin/courses/{id}/testo`: the centre is the **draft**, the left rail is the outline
(chapters and sections, sources, structure actions), the right panel is the **AI assistant**. `?chapter={id}` scrolls to
a chapter, `?chat=1` opens the assistant, `?ask=…` also puts a question in its box; the old addresses
(`/admin/editor/{id}`, `…/chapters/{id}`, and the course address with one of those parameters) lead there. The arrow at
the top left goes back to the course page.

* **Draft vs. PDF**: the draft is typeset by the same LaTeX as the PDF, with the course's own preamble, but **block by
  block** (see [AI-WORKSPACE.md](AI-WORKSPACE.md#draft-typeset-by-latex-blocks-as-svg)); the first view of a course takes
  a couple of seconds per chapter. The PDF tab compiles the whole document on demand; publishing always uses the full
  build.
* **Downloads**: *Sorgente LaTeX* and *PDF* (compiled now). The public pages offer the PDF and the LaTeX source of the
  published version from one «Scarica» menu (built with every publish).
* **Publishing is ON/OFF** (top bar of the course, or the switch on the Home). While ON, the public site shows the latest
  version: every change (imports, assistant) is rebuilt automatically about 2 minutes after the course stops changing
  (`LECTA_REPUBLISH_QUIET_S`). The public build is a clean full build without `\review` markers, split per chapter for
  the reader. If a rebuild fails, the previous PDF stays online and the course shows the error.
* There is no revision history: edits and imports write straight into the files; the only safety net is **Annulla** on
  the assistant's last answers.

## How a course is stored

Each course is one LaTeX project: `main.tex` (layout + a managed `\include` block), `preamble.tex` (generated from the
global template or the course's override), `chapters/NN-slug.tex`, `images/` and optionally `figures/*.tex` (TikZ
pictures, just the picture code). Images are placed with `\lectaimage[caption]{images/name.png}`;
`\lectafigure[caption]{name}` includes `figures/name.tex`; `\review{...}` is a highlighted note that disappears in
published builds. Chapters and positions are managed by the assistant and the outline (adding, renaming, moving and
deleting keep `main.tex` in step).

## The AI assistant

The panel on the right of a course is an **agent with tools**. Per turn it decides what to look at and what to do:

* **Reads everything in the course**: `course_overview`, `read_file` (any chapter, `main.tex`, `preamble.tex`,
  figures), `grep`, `find_related` (hybrid retrieval, see [IMPORT.md](IMPORT.md#which-chapter-placement)), the
  sources (`list_sources`, `read_source`, `view_source_page`: it can *look* at a slide or a photo) and images
  (`view_image`).
* **Takes pictures from the slides**: when the import left a figure out (a slide read only as text, or a picture
  dropped with the logos and decorations), ask for it in the chat (`extract_source_image`); the import's filters stay
  as they are.
* **Changes everything**: `edit_file` (exact search/replace), `write_file`, `create_chapter`, `rename_chapter`,
  `move_chapter`, `delete_chapter`, `delete_file`, `rename_file`, the preamble and `main.tex` (its chapter list stays
  managed); and verifies with `check_build` (real compile, errors with file and line).
* **Applied immediately.** The first write of a turn snapshots the course; the change card under the answer lists the
  files (+/− lines) and has **Annulla**, which restores the whole turn (refused if those files changed again since:
  undo the newer ones first).
* **Pick to ask**: click a block of the draft (Shift+click extends to a range of blocks) and a small menu offers three
  buttons. *Rimuovi* takes that passage out at once (the assistant also makes small formatting fixes around it, nothing
  else; mode `edit`). *Spiega* and *Correggi* open the chat with the passage as context and wait for what you want to
  know (mode `explain`, the answer never touches the document) or what to correct (mode `edit`).
* **Document feedback**: an earlier review (score, strengths, issues with a *Correggi* button) still shows in the chat
  and on the home page; the top bar no longer has a *Revisione AI* button.
* Turns run in the background (closing the tab doesn't stop them; reopen and the panel reattaches), one per course at a
  time, up to `agent_max_steps` model rounds (Settings → AI e costi, default 40). Uploaded material and course text
  reach the model fenced as untrusted data.

The assistant needs a model with tool support: Settings → Models → *Assistente AI* ([DEPLOY.md](DEPLOY.md#ai-providers)).

## Search and export

* **Search** (`/admin/search`): full-text over all course text (per-course text-search configuration, highlighted
  snippets), plus course/chapter titles and uploaded file names. The public `/search` covers published course and
  chapter titles only.
* **Export all** (Settings → Backup & export): a streamed zip of every course's sources (including the preamble in use),
  the latest draft PDF and the published PDF, optionally with the uploaded originals. Backups of the whole installation:
  [DEPLOY.md](DEPLOY.md#backup-and-restore).
