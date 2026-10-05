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

All planned work, ideas and bugs live on the [Features board](https://github.com/users/enricocovili/projects/1)
(GitHub Project 1 of `enricocovili`, linked to this repo). Use **`scripts/board.sh`** for everything on the board; it
needs `gh` logged in with the `repo` and `project` scopes (`gh auth status`). If `gh` is missing or not logged in,
say so and leave the board alone rather than working around it.

**Status** (`board.sh status N <key>`): `idea` 💡 Idea › `define` 🔍 Da definire › `ready` ✅ Pronta (planned, split
into sub-issues when it takes several commits) › `doing` 🚧 In corso › `done` ✔️ Fatta › `dropped` 🗑️ Scartata.
**Area**: the commit prefixes (Lessons, Draft, Assistant, Import, LaTeX, PDF, Publishing, Deploy, UI, Docs).
**Priorità**: P0 now, P1 next, P2 some day.

**Kinds**: every issue has exactly one kind label, chosen like this:

* `bug`: something that exists and is broken or wrong (code, docs, deployment). Starts in ✅ Pronta.
* `enhancement`: a change or addition already decided, concrete enough to do. Starts in ✅ Pronta.
* `idea`: worth exploring, not decided; may never be done. Starts in 💡 Idea.

People open issues through the forms in `.github/ISSUE_TEMPLATE/`; `.github/workflows/board.yml` puts them in the
column of their kind. Agents create them with `board.sh new`, which sets the label, the column, Area and Priorità.

### Working from an issue

When asked to work on an issue ("do #12", "fix the next bug", "what's ready?"):

1. Read the board (`board.sh list ready`, `board.sh list`) and the issue with its sub-issues and comments
   (`gh issue view N --comments`). Pick from ✅ Pronta by Priorità when not told which one.
2. An issue still in 💡 Idea or 🔍 Da definire is not planned yet: say what is missing and propose the plan (or ask)
   before writing code.
3. Set it to `doing`. If it takes several commits and has no sub-issues, create them first (`board.sh new … --parent
   N`, one per planned commit, titled like the commit) and list them as the step plan below.
4. Each commit that finishes an issue or sub-issue says `Closes #N` in its body (one line per issue). When the last
   sub-issue is done, the parent's last commit closes it too. Pushed to `main`, the issues close and the board moves
   them to ✔️ Fatta; until pushed, they stay in 🚧 In corso.
5. Wrap up with the commits made and the issues they close, plus any issue created along the way.

To have the agent work through the board on its own (ready, then da definire issues, by priority), run
`/board-solve [N | ready | define | max issues]` (`.claude/skills/board-solve/SKILL.md`). It follows the rules
here, never pushes, and asks questions on the issue instead of guessing product-level decisions.

### Recording new work

* Something found along the way and not fixed now becomes an issue (`board.sh new bug|enhancement|idea "Area: …"
  --area … --prio …`, body from stdin: what, why, where in the code), not a TODO comment or a line in
  `docs/STATUS.md`. Mention it in the wrap-up.
* Don't create issues for work done in the same session: the commits are the record.
* An issue that turns out to be wrong or unwanted: comment why, then `gh issue close N --reason "not planned"` and
  `board.sh status N dropped`.
* Titles follow the commit style (`Area: user-visible effect`) and are in Italian, like the board; bodies say what is
  wrong or wanted, why, and how to check it is done.
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
   that were made, noting any step that was skipped or left unfinished. When the
   request came from an issue, its sub-issues are the steps (see above).
