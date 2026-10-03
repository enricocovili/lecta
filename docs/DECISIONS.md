# Decisions log

Format: **decision** — reason. *Alternative:* main alternative considered.

## Repository / process

1. **`appunti/` is its own git repository**, not part of the parent `projects/` repo.
   The parent repo holds an unrelated collage editor with uncommitted deletions;
   committing there would mix histories. *Alternative:* commit into the parent repo.

## Deployment

2. **Three networks**: `internal` (`internal: true`, no internet), `egress` (backend and
   worker only, to reach AI providers), external `webnet` (frontend only).
   The DB and compile service can't reach the internet. *Alternative:* one default network.
3. **The compile container runs with `network_mode: none`** and serves HTTP over a unix
   socket on a shared volume. This meets "no network in the compile container"
   literally and still gives request/response semantics. *Alternative:* an internal
   network, or a file-drop queue.
4. **Two data volumes**: `app-data` (secrets, uploads, blobs; not mounted in the compile
   container) and `latex-work` (build dirs). This limits what a TeX sandbox escape could
   read. *Alternative:* a single volume.
5. **Frontend port 4321** (Astro's default). README: `lecta-frontend:4321`.
6. **Setup wizard requires a one-time setup code**, printed to the backend log and
   stored in `app-data/secrets/setup-code`, so whoever reaches the public URL first
   can't claim the instance. Still done entirely in the browser.
   *Alternative:* first visitor wins.
7. **Secure cookies are "auto"**: `Secure` is set when the request came over HTTPS
   (`X-Forwarded-Proto: https`), so local HTTP testing still works. Can be forced with the
   optional env `LECTA_COOKIE_SECURE=always`. *Alternative:* always Secure (breaks local HTTP tests).

## Access

8. **Anonymous access to any private API route or admin page returns 404**, not 401 or a
   redirect. `/login` and `/setup` are public; the (generic) 404 page has a small
   "sign in" link. *Alternative:* redirect to login (reveals which routes exist).
9. **405/422 can't leak private routes**: the guard runs before routing, so
   anonymous requests to private paths get 404 whatever the method or body.
   *Alternative:* per-route dependencies only (FastAPI parses bodies before
   dependencies, so malformed JSON would return 422 and reveal the route).
10. **UI language is English**, including the public site; generated notes use the
    course language. *Alternative:* Italian UI.
11. **Custom Postgres job queue** instead of procrastinate: a small table with
    `SKIP LOCKED` claims, LISTEN/NOTIFY, heartbeats and memoised steps
    (`job_steps`). Resumable jobs are central to the privacy gate: a job parks
    while waiting for approval and resumes without re-sending anything.
    *Alternative:* procrastinate (less control over progress, logs and resume).
12. **Manual edits coalesce into one revision per 10-minute window**; accepted AI
    proposals, restores, structure changes and publishes always create their own
    revision. *Alternative:* a revision per autosave.
13. **`main.tex` has a managed `\include` block** between marker comments, rewritten
    when chapters are added, renamed or reordered; the rest of `main.tex` is freely
    editable. *Alternative:* generate `main.tex` entirely (no manual edits).
14. **`preamble.tex` is generated at build time** from the course override or the
    global template, and is not an editable project file. *Alternative:* a
    per-course copy that would silently diverge from the template.

## LaTeX

15. **Persistent build dir + per-job temp dir**: each project keeps a persistent
    `latex-work/<course>/draft` dir (so latexmk is incremental); every run gets a
    fresh temporary HOME/TMPDIR/TEXMFVAR. Publishing uses a fresh dir (clean build).
    *Alternative:* copy everything into a temp dir per job (no incremental builds).
16. **Draft/publish flags through a generated root file**: `_root.tex` defines
    `\lectaincludeonly` / `\lectapublish` and then `\input{main}` (with
    `-jobname=main`). No `-usepretex` or shell tricks. *Alternative:* rewrite main.tex per build.
17. **Figures are body-only files** (`figures/x.tex` holds just the tikzpicture etc.),
    wrapped at build time in `standalone[varwidth]` with the course preamble, and
    included via `\lectafigure`. Cache key: figure source + preamble + engine +
    referenced images. *Alternative:* full standalone documents per figure.
18. **Per-chapter public PDFs are cut from the full build** using the PDF outline
    (chapter bookmarks from hyperref), rather than compiling each chapter separately.
    This is cheap on the weak CPU, and page numbers and cross-references stay
    consistent. *Alternative:* one `\includeonly` build per chapter.
19. **LuaTeX's Lua `io` library isn't restricted by `openin_any`**; rather than
    `--safer` (which breaks fontspec/luaotfload), the compile container simply has
    nothing sensitive mounted. Documented as a known limitation.
20. **The editor's preamble entry edits a per-course override.** The global
    template is edited in Settings → LaTeX.

## Providers & privacy gate

21. **Raw REST adapters instead of provider SDKs** (httpx through one transport module):
    fewer dependencies, and the isolation test can ban SDK imports outright.
    *Alternative:* official SDKs, wrapped.
22. **Transport tickets**: the transport refuses any call without a single-use,
    short-lived ticket bound to the provider's scheme+host, which only `gate.py`
    issues. This enforces "no request without the gate" at runtime, not just statically.
    *Alternative:* a static check alone.
23. **Provenance is attached by server code, never by models**: each content part
    lists refs (`source:<file>:p<n>`, `generated:<request_key>`, `chapter:…`, `user:…`,
    `prompt:…`). Job follow-ups are auto-approved only if every ref is covered by
    the approved plan, earlier approvals of the same job, or outputs of the same job.
    Parts without provenance are never auto-approved. *Alternative:* content
    hashing (fragile for crops/renders).
24. **Local providers skip approvals but are still audited**; the "local" flag is,
    like ZDR, my own attestation.
25. **Testing a connection / fetching models is also gated by ZDR** (it's a call to
    the provider). It sends only the API key and is audited as `authorization=test`.
    *Alternative:* allow tests for any provider.
26. **Fallback models**: used when the primary call fails. For jobs, only if the
    fallback provider was part of the approved plan; for single approvals, not at all
    (the approval names one provider). *Alternative:* re-ask for approval on fallback.
27. **The fake provider uses local-only request `meta`** (structured hints that real
    adapters never send) to produce deterministic, compilable outputs for every task.
28. **Deselected plan items are excluded from cloud requests.** Local-only processing
    (e.g. Markdown → LaTeX with pandoc) still uses them. *Alternative:* drop them entirely.

## Ingestion & review

29. **Typed PDF pages skip the vision model**: their text layer goes to the writer
    as text (cheaper, and less data leaves the server). Slides, annotated slides,
    scans and photos get a vision/handwriting call. Embedded images are still
    recreated as diagrams. *Alternative:* a vision call for every page.
30. **Images referenced from Markdown are figures, not pages**: they aren't
    transcribed as handwriting; they go to the diagram pipeline.
31. **Figures get temporary keys during generation** (`f<job>x<n>`) and are renamed
    to unique `figures/<chapter>-figN.tex` names when the proposal is built.
32. **Model LaTeX is sanitised before use**: `\documentclass`, `\usepackage`,
    `\input`/`\include`, `\write18`, `\openout`… lines are commented out and
    `\chapter` becomes `\section` (the chapter title is set by the app). The compile
    sandbox is a second barrier. *Alternative:* rely on the sandbox alone.
33. **Proposals start as `testing`** and become `pending` (shown for review) only after
    validation and a test build. The build uses `\includeonly` for the affected chapter.
34. **Merge edits must match exactly once**; if the model's search/replace edits don't
    apply, the new material is appended at the end with a `\review` note rather
    than dropped. *Alternative:* fail the proposal.
35. **Accepting after the base changed**: the stored search/replace edits are
    re-applied to the current file; if that fails the accept is refused (409).
36. **Uploading into a course without chapters** always creates a new chapter
    (the classifier isn't called).
37. **Unknown language → majority language of the sources**; mixed sources use the
    course language (if a course was chosen) and add a `\review` note.

## Categorisation

38. **Chunk size by words, not tokens**: ~60 words (≈100 word-piece tokens for this
    multilingual model, whose max input is 128) with a 12-word overlap. This avoids
    depending on the tokenizer internals. *Alternative:* exact tokenizer counts.
39. **Only the worker loads the embedding model** (it stays resident there); the
    backend never loads it. Placement runs in the worker, so the backend doesn't
    need it. *Alternative:* load it in both (double the RAM).
40. **Incremental indexing by polling** (every 30 s, chapters whose file blob or
    embedding model changed since `index_state`) instead of hooks on every
    autosave. It's simple, robust and cheap because unchanged chunks keep their
    embeddings (content hash). *Alternative:* enqueue a job on each save.
41. **Hybrid score** = 0.4 · lexical keyword coverage + 0.6 · normalised cosine
    ((cos − 0.2)/0.6, clipped), per chapter (best chunk). Lexical-only mode uses
    keyword coverage alone. Courses without chapters are also candidates.
42. **Inbox assignment is its own job with its own plan**: the generated notes are
    one plan item (`bundle:<id>`); merging still asks separately for the target
    chapter's source.

## Review chat

43. **Streaming uses POST + fetch** (SSE format parsed client-side) rather than
    EventSource, so the call that triggers a provider request carries the CSRF token.
    *Alternative:* EventSource over GET.
44. **The exact request is built when the message is posted** and stored with it. The
    approval shows that exact payload, and the send step uses it (with my edits).
    Nothing is rebuilt after approval.
45. **Chat edits use the same fenced ```edits JSON contract** (search/replace or full
    content). `main.tex` and new chapter files can't be created from the chat.
    Invalid edits are reported in the chat and not applied.
46. **Session grants cover "any message I type" (`user:*`) and the listed documents in
    any version (`chapter:<course>:<path>@*`)**, for one provider, until logout
    (grants are bound to the login session) or expiry. Anything else asks again.

## Polish

47. **Anonymous traffic is rate limited per IP** in the ASGI guard (300 req/min for
    public endpoints, 60/min for probes of private URLs, which then get 429 on every
    path, so existence still doesn't leak). Authenticated admin requests aren't limited.
48. **Export is streamed** (zipfile writing into a non-seekable sink, file by file), so
    large libraries don't need temporary space or memory. Uploaded originals are optional.
49. **Backup = pg_dump + tar of `app-data` and `secrets`**; build dirs are disposable.
    `restore.sh` resets the DB role password to the restored secret, so the restored
    database and `secrets` volume stay consistent. *Alternative:* raw volume copies
    (needs the DB stopped, less portable).
50. **A small recovery CLI** (`python -m app.cli reset-password | disable-2fa | logout-all | setup-code`)
    for a single-admin instance that has no other way to recover a lost password or 2FA device.

## Interface redesign

51. **The UI follows the prototype in `docs/Lecta — nuova interfaccia.pdf`** and is
    in Italian throughout (the backend's user-facing titles, warnings and errors too;
    prompts sent to models stay in English). Warm paper palette with a dark variant,
    Newsreader for titles, Inter for the UI, JetBrains Mono for code. The fonts are
    bundled from `@fontsource-variable/*`, so the CSP stays `font-src 'self'`.
52. **Theme: system by default, forced light/dark via the moon/sun button**, remembered
    per browser (`public/theme.js`, a blocking script in `<head>` so the page never flashes).
53. **Course status (Completata / In lavorazione / Errore) is derived, not stored**
    (`services/overview.py`): the latest draft/publish build, the latest finished job and
    active jobs. A failed compile shows up in the Home activity list even without a job.
54. **Mappa collegamenti is computed on request** (`services/concepts.py`) from section
    titles (normalised lexical match, always) plus stored embeddings when they are enabled
    (averaged per section, compared in SQL; the backend never loads the model), cached until
    a .tex file, chapter or index chunk changes.
55. **The AI review uses its own line diff** (`review/diff.ts`, Myers) with per-hunk
    Accetta / Scarta, side by side or inline with the preview PDF, inside the editor
    workspace. Undecided hunks are applied as proposed, as before.
56. **The public reader renders the published PDF with pdf.js** (document or presentation
    mode, index from the PDF outline). `/api/public/courses.zip` streams every published
    PDF ("Scarica tutto").

## Autonomous platform (after the redesign)

The platform was simplified to run by itself: no approvals, no review queue, no
revision history, no privacy gate, publishing ON/OFF. This supersedes #11 (parking
for approvals), #12 (revisions), #21–28 (privacy gate, tickets, provenance, ZDR,
local flag, plan deselection), #29–35 (vision per slide, diagrams → LaTeX,
proposals), #42 (inbox plans), #44–46 (chat approvals, grants, proposals) and #55
(per-hunk review), and #18 now serves only the public reader.

57. **Text + images instead of a vision call per slide.** PDFs are read locally
    (`pipeline/pdfextract.py`): text layer in reading order with sub/superscripts
    rebuilt from span positions, embedded images and vector drawings (clustered
    drawing commands) as pictures, theme graphics/logos/repeated footers dropped,
    beamer overlay steps skipped. A page is sent with its picture only when the
    text can't carry it (scans, garbled layers, ink, display math from CMEX fonts,
    formulas as images), and never alone: at most 4 page pictures per request.
    *Alternative:* a VLM call per page (what made imports slow).
58. **One reading step writes the LaTeX** (math included) for ~10 pages per request,
    all units in parallel; no second "study notes" rewrite, no diagram recreation.
    The output is a faithful conversion (no summarising, no translation); the
    prompt is editable. *Alternative:* keep `notes.write` (slower, rewrites twice).
59. **Plain-text replies with `%%` header lines** (`%%TITLE`, `%%FIGURE`, `%%DROP`,
    `%%END`) instead of JSON: JSON escapes silently corrupt `\frac`, `\beta`… A reply
    cut off at the output limit splits the unit in two and retries.
60. **Pictures are copied as image files** and placed with `\lectaimage`
    (plain `\includegraphics`, no standalone compile). A fallback definition is
    appended to every preamble at build time so courses with an overridden
    preamble still compile.
61. **Written straight into the course, exactly once.** A new chapter, or appended
    at the end of the target chapter (no AI merge). The course row is locked and the
    job step that records the write is inserted in the same transaction; "retry from
    scratch" keeps those steps. One compile check in a scratch project with at most
    `latex.autofix_iterations` (default 1) AI fixes. "Da smistare" stays for unsure
    placements; assigning from it writes directly. *Alternative:* always best guess.
62. **Grouping by folders**, not by a model call: one chapter per folder; at the
    root one per PDF/Markdown file and one for loose photos.
63. **Chat edits are shown as an inline diff in the editor** (CodeMirror unified
    merge view) and written only on *Accetta*; the change is stored on the reply
    message, a new message discards a pending one, and edits are re-applied if the
    file changed meanwhile. *Alternative:* apply directly without a look.
64. **Publishing ON/OFF, always the latest version.** One `Publication` row per
    course, replaced on every build. A worker loop rebuilds published courses whose
    `updated_at` is newer than the last build (`publish_requested_at`) after 2 minutes
    of quiet; a failed build keeps the previous PDF online and isn't retried until
    something changes. *Alternative:* a snapshot per click.
65. **No privacy features beyond a disclaimer**; the gateway (`egress/gate.py`) still
    centralises provider calls, fallbacks, the semaphore and a cost log (metadata
    only, no payload copies). Nonce fencing of uploaded content stays (prompt
    injection defence).

## Study text from the class notes

The faithful conversion of #58 read like the slides themselves. The import now
writes a text to study from, with the student's class notes as its backbone.
This supersedes the "faithful conversion" part of #58 (the reading step stays,
as the input of the writing step) and #62's root rule.

66. **The class notes (`.md`/`.txt`) are the backbone of the text.** A writing step
    (`pipeline/compose.py`, prompt `notes.compose`) turns notes + material into
    prose: the notes' order and points, completed with the material's definitions,
    formulas, derivations, examples and useful pictures; compact, but no concept
    lost. Without notes the material is summarised the same way. The reading step
    still converts the material faithfully first, so nothing is lost before the
    writer sees it and formulas stay exact. *Alternative:* summarise while reading
    (one call less per unit, but each unit would be condensed without seeing the
    notes, and the notes couldn't give the structure).
67. **Long texts are written in parts, in parallel**: slices of the notes (cut at
    headings) each with the whole material, or its most relevant sections beyond
    150k characters (word overlap); without notes, slices of the material (cut at
    sections, never inside an environment). The number of parts comes from the
    expected length against the output budget; a reply cut off splits its part.
68. **The upload asks when there are no notes.** `finish` answers 409 `no_notes`
    unless `without_notes` is set (zips are looked into); the upload stays open, so
    the notes can be added to it. Quick uploads from the phone are exempt: their
    photos are the notes.
69. **Grouping pairs notes with their slides**: at the root, one notes file takes
    every file; several notes files each take the PDFs whose names match (shared
    words, lecture numbers must agree); pictures in `img/`, `figures/`, … belong to
    their parent folder. The text is written in the course's language, else the
    notes', else the material's.


## AI workspace (no LaTeX editing in the UI)

The app is used by a student, not a LaTeX author: the user reads a draft and works
with an assistant that can touch everything. API contract in `docs/AI-WORKSPACE.md`.

70. **The UI has no LaTeX editor.** CodeMirror, the file tree, diagnostics, SyncTeX and
    the inline diff bar are gone. LaTeX stays the storage format (public PDF, `.tex`
    download, TikZ, the existing import pipeline), so nothing had to be migrated.
    *Alternative:* Markdown as the canonical format (fast preview for free, but the import
    pipeline, the migration and every test would change for no user-visible gain).
71. **The draft is HTML rendered by pandoc, not a PDF.** `services/preview.py`: the chapter
    source is annotated with line markers, converted (`pandoc -f latex -t html --mathjax`,
    ~30–100 ms) and post-processed by an allow-list sanitizer: blocks carry `data-line`,
    maths keeps its TeX (`data-tex`, KaTeX in the browser), theorem boxes / review notes /
    pictures / placeholders get classes. TikZ drawings are placeholders. The real PDF is
    compiled on demand and for publishing. *Alternative:* a lighter, per-chapter PDF build
    (still seconds per change, and no way to map a selection back to the source).
72. **The assistant is an agent with tools, provider-neutral.** Tool calling is added to
    the gateway and the Anthropic, OpenAI-compatible and Gemini adapters (`ToolSpec`,
    `tool_call` / `tool_result` parts, streamed). The tools (`pipeline/agent_tools.py`)
    read the whole course (files, sources, page images) and change all of it, including
    `main.tex` (its chapter list stays managed), the preamble and chapters. This replaces
    the one-shot chat that saw one file and could not touch `main.tex` or add chapters.
    *Alternative:* a text protocol for "edits" (works with any model, but fragile and
    limited to one round trip).
73. **Changes are applied immediately; the safety net is undo of the turn.** The first write
    of a turn snapshots the course (files with blobs, chapters, preamble); the change card
    has Annulla, which restores it, and is refused when those files changed again (undo
    newest first). It's the only history: no revision list. *Alternative:* diff + Accetta
    (the old flow: one click per change, which is what "unlimited power" removes).
74. **A turn runs in the background and its events are replayable.** `POST messages` starts
    it and returns; `GET replies/{id}/events` replays and follows (`text`, `tool`,
    `tool_done`, `change`, `done`, `error`). Closing the tab doesn't stop it; one turn per
    course; `agent_max_steps` model rounds (default 40). After a server restart an unfinished
    reply is marked as interrupted. *Alternative:* stream inside the POST (a dropped
    connection would leave half-applied edits with no record).
75. **Selecting text is the main way to ask.** The draft maps a selection to
    `{chapter_id, from_line, to_line, text}`; the assistant finds it with `grep`, so it
    works even where the block map is coarse. One-click actions send fixed prompts
    (explain → mode `explain`, no writing tools).
76. **Document feedback is a structured review.** Mode `review` (read-only tools) ends with a
    `review` block (verdict, score, strengths, issues with a ready-made `fix` prompt);
    the Home and the course show the latest one, marked stale when the course changed since.
77. **The LaTeX source is downloadable in both views.** Working version: `source.zip` built on
    demand. Public: the publish job stores the zip of the published project
    (`\lectapublish` set, so review notes stay hidden) next to the PDF; courses published
    before get one automatic rebuild.
78. **Taking notes in class is a first-class workspace, and it is the import's input.** A *lesson* is a PDF of
    slides plus what the student writes on it while the lecturer talks: Markdown notes next to each slide and
    pen strokes over it (Xournal++ style, for mouse, stylus and finger). Generating the text hands the lesson to the
    existing `ingest` job, so grouping, reading, writing, placement and the compile check are unchanged (one
    group per lesson). *Alternative:* a separate note-taking app that exports a `.md` (loses the link between a
    note and its slide, and the handwriting).
79. **Ink is stored as data, not as an image.** Strokes `{t, c, w, p: [x, y, pressure…]}` in units of the page's
    width, per page (JSONB), so any zoom/screen fits, the server can draw the same strokes on the PDF
    (PyMuPDF: annotated download, pictures for the model) and undo/erase work on strokes. *Alternative:* a
    bitmap layer per page (no erasing of single strokes, resolution-bound, heavier).
80. **The model sees the slide with the handwriting, and the notes carry the slide numbers.** Only slides
    that have ink are sent as a picture (with the strokes drawn on); the notes file has a `## Slide N` section
    per slide, the reading step marks slides with `% slide N` comments and the writing prompt pairs them
    (and never mentions the numbers). That is the "strong bond" between the two, at no extra call.
81. **A lesson is snapshotted when the text is generated.** The job works on a copy (blob) of the pages, so
    writing during the import cannot change what is being read, and a retry after a restart reads the same
    thing. The lesson's files (annotated pictures, notes `.md`, handwritten pages) are created by the job's first
    step, not by the request, so the button answers at once.
82. **Saving is automatic, on demand, and tolerant of a bad connection.** Edits apply on screen at once and are
    mirrored in `localStorage`; every minute, on Ctrl+S (which replaces the browser's "save page", also while
    typing), when the tab is hidden and before structural changes, the unsaved pages are sent (`PUT …/pages/{id}`
    replaces only the fields sent). Failures are kept in memory, retried with backoff, and restored on the next
    visit if the server's page did not change (its `version`). Last write wins (one user).
83. **Finger scrolls, pen writes.** On touch screens `touch-action: none` is needed for the pen to draw, so the
    surface scrolls by hand when a finger drags (unless "Dito scrive"), and ignores touches for ~1 s after a
    pen was seen (palm rejection). The stylus' eraser end erases whatever tool is selected.
84. **Guidelines are per subject and asked at generation time.** `courses.guidelines` (empty = automatic). Every
    generation into a chosen subject shows them (prefilled) with a notice when there are none, so the choice is
    conscious but never blocks; «ricorda» keeps them. They travel in the job's payload (so a run is
    reproducible) and fall back to the course's for imports that say nothing. An upload with no subject chosen
    cannot ask (the target is decided after the text is written).
85. **The default writing style is fixed in the prompt, guidelines win over it.** Concise but clear text, tables
    (`tabular` + booktabs) for anything listing items with shared attributes or categories, and small pictures
    beside the text that explains them. One vocabulary of three picture commands, defined in the template and in
    `COMPAT_TAIL` (for courses with their own preamble): `\lectaimage[caption]{img}` (small, alone),
    `\lectaimagewithtext[caption]{img}{text}` (small, with its explanation beside it) and
    `\lectaimagepair{cap 1}{img 1}{cap 2}{img 2}`. An empty image name leaves the text alone, which is how the
    import drops a repeated or unknown picture without breaking the command.
86. **Tables show up as tables in the PDF.** Text written without booktabs rules was typeset as loose lines while the
    draft drew a grid. When the PDF is built (`projects.materialize`) every `tabular`/`tabularx` without rules
    (`\hline`, booktabs, `|` in the spec) gets `\toprule`/`\bottomrule` and `\noindent` on the lines it already uses
    (`services/tables`); the stored chapter and the source download are untouched. The draft shows the same booktabs
    look (rule above, under the header, below; no grid). The PDF is compiled on request or when published, never as
    part of editing.
87. **The draft must not lose or distort what the PDF shows.** The HTML draft is a pandoc conversion, so a macro it
    does not know silently vanishes, text included (this is how whole `\lectaimagewithtext` blocks disappeared
    from a chapter). The three picture commands are therefore parsed in one place (`services/latexmacros`, used by
    the import, the draft and the plain-text extraction) and the draft draws them with the limits of the course's own
    definitions in its preamble (`preview.image_limits`: widest, tallest, as the PDF typesets them), via CSS variables
    and container units. Unknown macros of a custom preamble are still dropped by pandoc: extend the vocabulary rather
    than relying on `\newcommand` expansion.
88. **One history for undo.** The lesson editor's Ctrl+Z was split: strokes had the app's undo, the text the text field's own,
    so after writing and then drawing it undid the text. All of it is now one stack in the order things were done
    (`useLesson`): strokes, erasing, typed notes and removed pages. Notes are kept as small patches
    (`lessons/history.ts`: start, text cut, text put) and typing is merged into one step while the pauses are under a second
    and each edit is small (a paste is a step of its own). Removing a page is undoable because the page comes back with its
    **own id** (`POST …/pages/restore`; the id must have been handed out by the sequence already and not be taken), so the rest
    of the history still points at it. Undo and redo run one after the other because restoring asks the server.
89. **Share links are bearer secrets in the address, with two modes.** `lesson_shares`: a 192-bit token per lesson and mode
    (read | write), shown to the owner as `/s/<token>`, revoked by deleting the row (and gone with the lesson). The public routes
    mirror the owner's (`/api/public/lesson/{token}/…`) so one editor serves both; only the token decides what is allowed
    (a read link gets 403 on any write), and none of the owner's side (guidelines, chapter, generating, sharing, renaming,
    deleting) is reachable. Several people editing are reconciled by **polling** (`POST …/sync` with the versions the caller
    has: the new order and the pages whose version differs): a page the user has unsaved work on is never replaced, and a save
    replaces the whole notes/strokes of that page (last save wins), accepted for a handful of classmates. The pages are kept
    out of search engines (noindex) and the site's referrer policy keeps the token from leaving it.
90. **Passkeys are a way in, not a second factor on top.** WebAuthn through `webauthn` (py_webauthn): registration asks for a
    discoverable credential with user verification (so the sign-in page needs no username and works with Bitwarden or any
    manager), no attestation. Signing in with one skips the password **and** the TOTP code, since a verified passkey is
    possession + inherence/knowledge by itself; the password remains as the fallback (nothing to lock oneself out with).
    The relying party is the host the browser is on: the `Origin` header must match the request's `Host` (so another site cannot
    start a ceremony), the challenge is kept in memory, single use, five minutes (one backend process, like the rate limiters).
    Unknown, unverified, replayed or wrong-origin answers are the same 401 and count as login failures.
91. **The product is called Lecta, and the LaTeX names moved with it.** The macros the stored text uses (`\lectaimage`,
    `\lectaimagewithtext`, `\lectaimagepair`, `\lectafigure`, and the build-time `\lectapublish`, `\lectalang`, …), the
    managed block of `main.tex` (`% lecta:chapters:begin/end`) and the lesson blocks (`% ==== Lecta: lezione N`) are part of
    the stored projects, so migration `0017` rewrites them instead of the app understanding both spellings: every text file
    of a course becomes a new blob (the old one stays on disk), the undo snapshots of the assistant's answers follow the
    new hashes (undo compares the snapshot with the current files by hash and would otherwise refuse), the chapter index
    follows too (nothing is re-indexed), and every other text/JSON column (preambles, prompts, settings, import staging,
    job results) gets the same rewrite. It matches macro and marker forms only: «appunti» as a word, file names such as
    `appunti.jpg` and a course called «Appunti di Fisica» are left alone. The migration is not reversible.
92. **Moving an installation to the Lecta name is a restore, not an in-place rename.** The compose project name prefixes
    every volume and the database and its role carry the name, so renaming in place would either orphan the data or need
    `ALTER ROLE`/volume copies on a stopped Postgres. The migration script (since removed, see git history) instead took the backup the app already
    knows how to take and restores it into a fresh `lecta` stack with `scripts/restore.sh` (the restored secrets carry the
    database password; `pg_restore --no-owner` puts the objects under the new role), so the same path is what gets
    rehearsed and what runs. Migrations 0016/0017 then rewrite the data on the first boot. The old containers and volumes
    are never deleted by the script (the way back); the old frontend is stopped first, and if anything fails before the new
    stack starts, the containers it stopped are started again. Browsers keep their remembered settings (`theme.js` copies
    `appunti.*` / `appunti:*` localStorage keys to `lecta.*` / `lecta:*` once) but sign in again, because the session cookie
    changed name. `backups/` is now git-ignored.
93. **The frontend container is named `lecta-frontend`** (`container_name`), because the project prefix plus the service
    name gave `lecta-lecta-frontend-1`. The service keeps its name: Compose adds it as a DNS alias on `webnet`, and a
    generic `frontend` could clash with another stack there. A fixed name means two copies of the stack cannot run side
    by side, so `scripts/smoke.override.yml` gives the isolated test copy `lecta-smoke-frontend`. *Alternative:* a service
    called `frontend` with a `lecta-frontend` network alias (leaves a `frontend` alias on the shared network).
94. **The draft is typeset by the real LaTeX, block by block, instead of pandoc HTML** (replaces 71 and 87). The HTML
    draft kept drifting from the PDF (custom macros dropped, TikZ as placeholders, tables, numbering, references). Now a
    chapter is split into blocks (`services/blocks`: paragraphs, headings, whole environments, never inside braces,
    environments or verbatim) and one engine run of a wrapper document (`services/draft`: the course's `main.tex`
    preamble, each block from its own file on its own page, page styles off) typesets them; `mutool` turns the pages into
    SVG cropped to the ink, sized against the text width so every block shares one scale. Blocks are cached as
    *variants*: their text and inputs (pictures, figures, the values of the labels they cite, preamble) plus the counter
    state they started from and the one they left. Python walks the chain to see whether anything is missing; the wrapper
    walks it again in TeX with the real counters and skips every block whose cached start state matches, so an edit
    typesets that block and the numbered blocks after it, and a block that changes and prints no counter (most
    paragraphs) fits any state. Labels are fed from the aux of earlier runs; when a run changes one, a second pass
    typesets the blocks that cite it. With pdflatex the preamble is precompiled once with mylatexformat (~0.5 s instead
    of ~1.6 s per run with TikZ & co.). Selection is per block (click, Shift+click) and sends the blocks' LaTeX.
    *Alternatives:* a per-section PDF in pdf.js with SyncTeX (bigger units, slower, fake page breaks); pandoc HTML with
    LaTeX-rendered islands for what it gets wrong (the prose would still not look like the PDF). *Costs:* text in the
    draft is pictures (not selectable as text, and small on a phone), and the first view of a course typesets every
    chapter (a couple of seconds each).

## A simpler interface (#17)

95. **Every place is reached one way.** The sidebar lists only the places of work (Materie, Lezioni, Da smistare, Mappa) and the
    subjects; the Home opens from the «Lecta» title like on most sites, search is the bar at the top (on a phone, the
    lens in the top bar), Attività and Impostazioni are in the user menu. The phone's bottom bar has no Home either.
    *Why:* the same link in two places made the sidebar long and the app harder to read.
96. **Material comes in from the lessons only.** The upload page (`/admin/upload`), the phone page (`/admin/quick`) and
    `POST /api/uploads…` are gone: almost always the material belonged to the subject already open, and a lesson always
    has its subject, so the import never has to guess the course. The import keeps its `Upload` rows (a lesson makes one,
    `via = "lesson"`), the source files and the manifest; the extraction still understands zips, photos and folders, which
    only the tests feed it now. The phone's home-screen icon opens the lessons.
