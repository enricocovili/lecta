# From a lesson to the study text (import)

Material comes in from the **lessons** only: there is no upload of loose files. What **Integra appunti** hands the
import (slides, notes, annotated slides) and how a lesson stays linked to its chapter is in
[LESSONS.md](LESSONS.md#from-the-lesson-to-the-text). The lesson always belongs to a course, so the text always lands in
that course. The class notes are the backbone of the text; a lesson with slides and no notes gets a summary of the
slides.

The job runs to the end by itself (Estrazione › Lettura › Stesura › Inserimento):

1. **Extraction** (local, no AI). PDFs: the text layer in reading order with sub/superscripts rebuilt (`x^{2}`,
   `x_{i}`), headings and slide titles; repeated headers/footers, logos and theme graphics dropped; embedded images and
   vector drawings (plots, diagrams, found by clustering the drawing commands) become images with a `[[IMG id]]` marker
   where they sit; beamer overlay steps and blank pages are skipped. A page is read *with its picture* only when text
   isn't enough: scans, garbled text layers, handwritten ink, display math, formulas stored as images. Notes are kept as
   written, a section per slide; a page written by hand is a picture to read.
2. **Grouping**: a lesson is one group: its notes with its slides.
3. **Reading**: the material in units of ~10 pages of one file (Settings → AI e costi), all in parallel, converted
   faithfully to LaTeX: math as LaTeX, every picture placed with `\lectaimage`, nothing left out (prompt `read.pages`).
   A reply cut off at the output limit splits the unit in two.
4. **Writing** (prompt `notes.compose`): the study text of each chapter, in prose. It follows the order and points of
   the class notes and completes them with the material (definitions, formulas, derivations, examples, the useful plots
   and diagrams), compact but without losing concepts; without notes the material is summarised the same way. It is
   written in the course's language (or the notes'). A long text is written in parts in parallel (slices of the notes,
   each with the relevant material), and a reply cut off splits its part.
5. **Placement** (see below) → **compile check** in a scratch project with at most one automatic AI fix (Settings →
   LaTeX) → **written into the course**: a new chapter, the lesson's own chapter, or appended to the chosen/matching
   chapter of the lesson's course. Nothing is ever left waiting for you to file it.

Every step is memoised: a retried job never repeats finished work or re-sends anything, and writing into a course
happens exactly once.

## Which chapter (placement)

Candidates come from **hybrid retrieval** over chapter content:

* lexical: PostgreSQL full-text search with each course's text-search configuration (italian / english / … / simple);
* semantic: local embeddings with `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (quantised ONNX via
  fastembed, CPU only, 384-d vectors in pgvector). Chapters are split into ~100-token windows (LaTeX stripped, each
  prefixed with its chapter/section heading), and only chunks whose content hash changed are re-embedded. Indexing runs
  in the worker at low priority with ONNX threads capped (default 2). The model is downloaded when the image is built
  and stays resident in the worker.

On first boot the worker runs a short **benchmark** (shown in Settings → Embeddings). If embeddings are off,
unavailable or slower than the configured threshold, retrieval **falls back to lexical-only** automatically.

The classification model gets the notes plus the top-k candidates' titles and section outlines (only chapters of the
lesson's course), and returns the best placement with confidence and rationale: append to one of those chapters or
start a new one. When nothing fits, or the lesson asked for it («Un capitolo nuovo»), the text becomes a new chapter of
the course.
