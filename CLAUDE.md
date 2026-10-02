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
