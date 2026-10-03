# Lecta — working notes for Claude Code

See `README.md` for running, testing and architecture, and `docs/` for the plan,
decisions and status.

## Commits

* **One atomic commit per feature or change.** Each commit does one thing and
  leaves the tree building and the tests passing. Don't batch unrelated changes
  into one commit, and don't leave a finished change uncommitted while starting
  the next one.
* Split mixed work before committing: stage by file or hunk (`git add -p`) so a
  fix, a refactor and a feature end up in separate commits.
* Keep code, tests and the docs that describe it (README, `docs/`) in the same
  commit as the change they belong to.
* Message style follows the history: a short imperative-free summary prefixed by
  the area (`Lessons: …`, `PDF: …`, `Draft: …`, `Writing prompt: …`), stating the
  user-visible effect.

## Issues and the board

Plans and ideas live on the [Features board](https://github.com/users/enricocovili/projects/1) (`gh project … 1
--owner enricocovili`), linked to this repo. Status: 💡 Idea (draft item) › 🔍 Da definire (issue) › ✅ Pronta (split
into sub-issues) › 🚧 In corso › ✔️ Fatta › 🗑️ Scartata; fields **Area** (the commit prefixes) and **Priorità** (P0–P2).

* **Two kinds of issue**, by label: `idea` (to explore, not decided: starts in 💡 Idea) and `bug` (a bug or problem
  that needs fixing: starts in ✅ Pronta; `.github/workflows/board.yml` places them). New issues come from the forms in
  `.github/ISSUE_TEMPLATE/`; an issue made
  with `gh issue create` gets one of the two labels. Planned work that is neither (a feature already decided, a
  verification) has no kind label.
* **Starting from an issue** ("do #12"): read it and its sub-issues with `gh issue view`, set it to 🚧 In corso, and
  use its sub-issues as the step list below (create them first if the issue has none and the work needs several
  commits).
* **Each commit that finishes an issue or sub-issue** says `Closes #N` in its body; pushed to `main`, it closes the
  issue and the board moves it to ✔️ Fatta.
* **Work that shows up along the way** and isn't done now becomes a new issue (or a draft item for a raw idea), not a
  TODO comment or a note in `docs/STATUS.md`.
* `docs/` keeps the *how* and *why* (contracts, `DECISIONS.md`); the board keeps the *what* and *when*.

## Big prompts: split into steps, one commit each

When a prompt asks for several things at once (a multi-part feature, a list of
changes, a large refactor), don't tackle it as one lump:

1. **Plan first.** Before touching code, break the request into an ordered list
   of defined steps, each small enough to be a single atomic commit (one
   feature, fix or refactor, with its tests and docs). Say the list out loud —
   a short numbered list with the intended commit summary for each step.
2. **Order the steps so every commit stands alone.** Put preparatory refactors
   and fixes first, then the features that build on them. No step may leave the
   tree broken or rely on a later step to make the tests pass.
3. **Run the steps one at a time.** For each step: implement it, run the
   relevant checks, commit it (following the rules above), and only then start
   the next. Never carry a finished step's changes into the next one.
4. **Keep the plan honest.** If a step turns out bigger than expected, split it
   further; if new work shows up mid-way, add it as its own step rather than
   folding it into the current one. Mention any change to the plan.
5. **Ask only when blocked.** If the request is ambiguous in a way that changes
   the steps, ask before starting; otherwise pick sensible defaults and go.
6. **Wrap up with the list.** At the end, summarise the steps as the commits
   that were made, noting any step that was skipped or left unfinished.
