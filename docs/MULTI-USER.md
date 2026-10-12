# Multi-user Lecta — implementation plan

> Written before implementation (2026-10-12), so a fresh session can pick it up. Decisions taken here go into
> `DECISIONS.md` (new section «Più utenti») as each step lands; the board tracks progress. Delete this file (or fold
> what is still useful into `DECISIONS.md`) when the last step is done.

## Goal

Lecta today has exactly one account (the admin). Make it multi-user, **closed to sign-ups**: only an admin creates
invite links; whoever opens one creates an account with a **passkey** (no password) and the link is consumed.

## Product decisions (taken with the owner)

* **Content is per user.** Each user owns their courses (and everything under them: chapters, files, lessons, labs,
  sources, jobs). Existing data becomes the current admin's.
* **Roles**, each including the ones before it:

  | role   | own content | others' **published** courses | others' unpublished content | AI, import, compile | publish, `/s/` share links | settings, providers, prompts, costs, users |
  |--------|-------------|------------------------------|-----------------------------|---------------------|----------------------------|--------------------------------------------|
  | viewer | — (cannot create) | read + comment/suggest | read | no | no | no |
  | editor | full        | read + comment/suggest       | **not visible**             | on own content only | no | no |
  | admin  | full        | full                         | full                        | yes                 | yes | yes |

  So: viewers see everything read-only; editors see their own content plus everyone's published courses; admins see
  and change everything. Publishing and lesson share links stay admin-only (they expose content to anonymous people).
* **Assistant conversations belong to whoever started them.** A user sees their own; an admin sees everyone's (with
  the author's name). «Non capita» marks and the last open page of a lesson stay shared per lesson for now.
* **Comments and suggestions on published content.** Any signed-in user (viewer and up) can leave a *comment* (a note)
  or a *suggestion* (a proposed replacement of the block's LaTeX) on a block of another user's **published** course,
  from the in-app read-only draft view. The course's author sees them in their own draft (edit workflow) and accepts,
  rejects or resolves them. The public pdf.js reader is untouched (anonymous visitors can't comment).

## Defaults chosen (not asked; change in `DECISIONS.md` if they turn out wrong)

* Invite: role chosen at creation, optional note, **expires after 7 days**, shown **once** (only its sha256 is stored,
  like session tokens), revocable while unused. Consumed atomically (`UPDATE … WHERE used_at IS NULL AND expires_at >
  now() RETURNING`) in the same transaction that creates the user and the passkey.
* The invited person picks their username on the invite page (same pattern as setup: `^[A-Za-z0-9._@-]+$`, unique).
* Invited users have **no password** (`password_hash` NULL): password login and TOTP don't apply to them; they can add
  more passkeys in Settings → Account. The first admin keeps the password as a fallback, as today.
* **Recovery link**: an invite bound to an existing user; accepting it adds a passkey to that user instead of creating
  one (a lost passkey must not orphan a user's content).
* Users are **disabled**, not deleted (can't sign in, sessions dropped, content kept and still visible to admins).
  Deleting is allowed only for a user with no courses. The last active admin cannot be demoted or disabled.
* Response codes: anonymous → 404 everywhere (the guard, unchanged); signed in but role too low → **403**; someone
  else's resource you may not see → **404** (editors can't probe what others have).
* Course slugs stay globally unique (public URLs are `/courses/<slug>`).
* AI providers stay shared (the admin's keys). Every AI call records the user; the costs page splits by user.
* A published course always follows its latest version (decision 64), so an editor's edits to a course the admin
  published are republished automatically. Kept.
* Comment/suggestion visibility: each commenter sees their own notes; the course owner and admins see all.
  A note's author can edit or delete it while it is open. Unpublishing keeps the notes but allows no new ones.
* Authenticated requests stay unthrottled (as today for the admin).

## Where things are today

* `backend/app/models.py`: `AdminUser` (table `admin_user`), `Passkey` and `Session` (FK `admin_user.id`). `Course` is
  the root of all content (`courses.slug` unique); `Job.course_id`, `Upload.target_course_id`, `ChatSession`
  (`course_id`, `lab_id`, `lesson_id`), `AuditLog` (no user), `LessonShare`.
* `backend/app/security/auth.py`: sessions, CSRF, `require_admin` (404 for anonymous, CSRF on unsafe methods),
  `get_admin_user` (the single row, used for setup status and password login).
* `backend/app/security/guard.py`: `PrivateGuard` — every non-public `/api` path needs a session before routing;
  `PUBLIC_EXACT` / `PUBLIC_PREFIXES` list the public ones.
* `backend/app/api/*.py`: almost every router is `APIRouter(dependencies=[Depends(require_admin)])`; `passkeys.py`
  (registration for the signed-in user, public `/auth/passkey/options|login`), `auth.py` (setup, login, account),
  `shared.py` (public `/api/public/lesson/{token}/…`), `public.py` (public site).
* `backend/app/api/courses.py:99` `load_course` / `load_chapter`: the helpers most routers use to fetch a course — the
  natural place for the ownership check.
* `backend/app/services/retrieval.py`: `lexical` / `semantic` with `course_id=None` search **every** course; used by
  `pipeline/placement.py` and `pipeline/agent_tools.py`. Must be limited to the owner's courses (otherwise editor A's
  assistant reads editor B's notes).
* `backend/app/api/search.py`: `/search` and `/export` iterate every course.
* `backend/app/cli.py`: `reset-password`, `disable-2fa`, `logout-all`, `setup-code` assume a single admin (`_admin`).
* `backend/app/api/preview.py`: draft and `PUT …/chapters/{id}/block` (a block is `start`/`end`/`src`, refused with
  409 if `src` changed); blocks come from `services/blocks.py`.
* Frontend: `frontend/src/middleware.ts` gates `/admin/*` on "authenticated" only, via `/api/auth/me`;
  `components/AuthForms.tsx`, `lib/passkey.ts`, `components/settings/AccountSettings.tsx`, `SettingsPage.tsx`.
* Tests: `backend/tests/test_auth.py`, `test_passkeys.py`, `test_boundary.py`; e2e `e2e/tests/08-passkeys.spec.ts`
  (virtual authenticator).

## Safety net (build it early, extend it each step)

1. **Route table test**: enumerate `app.routes`; every `/api` route must be classified (public, viewer, editor, admin)
   — an unclassified route fails the test.
2. **Cross-user test**: two editors, A and B, each with a course (lesson, lab, chapter, file, job, upload, chat…).
   Call every parametrized route as B with A's ids and expect 404 (unpublished) or 403/404 for writes (published).
   Plus: a viewer gets 403 on every unsafe route except comments; an editor gets 403 on admin routes.

## Steps (one atomic commit each, in order)

Each commit carries its tests and the docs it changes (`DECISIONS.md` «Più utenti», `ARCHITECTURE.md` sessions and
boundary, `DEPLOY.md` for the CLI, `README.md` if the presentation changes). Commit prefixes per `CLAUDE.md`.

1. **Auth: `admin_user` becomes `users`, with a role and a disabled flag.** Migration renames the table (FKs of
   `passkeys` and `sessions` follow), adds `role` (`viewer|editor|admin`, existing row → `admin`), `disabled_at`,
   makes `password_hash` nullable. Model `AdminUser` → `User`. Password login looks the user up by username (keep the
   timing equaliser; users without a password always fail). `setup/status` = "no admin exists". `load_session`
   rejects disabled users. CLI commands take `--user` (default: the only admin). No visible change.
2. **Auth: access levels per route.** The guard only requires an active session; `require_user`,
   `require_role("editor")`, `require_role("admin")` dependencies (CSRF as today). Classify every router/endpoint:
   admin — settings, index, providers, prompts, costs/audit, publish/unpublish, lesson shares, diag; editor — unsafe
   methods on content, chat, generate, compile, uploads, job cancel/retry/delete; viewer — safe reads, own account
   (passkeys, sessions, logout). Add the route-table test. Only admins exist yet, so nothing visible changes.
3. **Courses: every course has an owner.** `courses.owner_id` (existing → the admin). One access helper (extend
   `load_course`): `read` allowed to owner, admin, viewer, or anyone if published; `write` to owner or admin. 404 when
   not readable, 403 when readable but not writable. Apply in courses, editor, preview, publish routers; filter
   `/courses` and `/tree`. Creating a course sets the owner.
4. **Lessons and labs: same checks.** Lessons, pages, `unclear`, last page, labs, lab files and comments, uploads,
   sources, blobs (`/blobs/items|figures/{id}`) resolve to their course and go through the helper. Lessons and labs of
   others are never readable by editors (they are not published content).
5. **Jobs and search: per owner.** `jobs.user_id` (who started it) and filters for `/jobs`, job events/logs;
   `/dashboard`, `/search`, `/concepts`, `/export` limited to readable courses; `retrieval`, `placement` and the agent
   tools limited to the owner's courses (pass an explicit `course_ids` set instead of `None`). Add the cross-user test.
6. **Assistant: conversations belong to their author.** `chat_sessions.user_id` and `audit_log.user_id` (existing →
   the admin); lists show own conversations, admins see all with the author; the AI is refused on courses the user
   can't write. Costs page splits by user.
7. **Auth: invite links (backend).** Table `invites` (`token_hash`, `role`, `note`, `user_id` NULL — set only for
   recovery links in step 10, `created_by`, `created_at`, `expires_at`, `used_at`, `used_by`). Admin: create (returns
   the link once), list pending, revoke. Public (add `/api/invite/` to the guard's public prefixes, rate limited like
   login, unknown/used/expired token → 404): `GET /api/invite/{token}` (role, note, still valid), `POST
   /api/invite/{token}/options` (WebAuthn registration options, does not consume), `POST /api/invite/{token}/accept`
   (username + credential: verify, create the passkey-only user with the invite's role, store the passkey, consume
   the invite, sign in) — all in one transaction. Reuse the ceremony code in `api/passkeys.py`.
8. **UI: the invite page `/invito/<token>`.** Shows the role, asks for a username, «Crea la passkey», lands in
   `/admin`. Expired/used links show the generic 404. E2E with the virtual authenticator (extend `08-passkeys`).
9. **Settings: «Utenti» section (admin only).** New invite (role, note, copy the link — shown once), pending invites
   with revoke, users (role, created, last seen, passkeys count, disabled) with change role, disable/re-enable, sign
   out everywhere, delete (only without courses). Last active admin protected (backend refuses too).
10. **Auth: recovery link for a user who lost their passkey.** «Link di recupero» on a user in «Utenti» creates an
    invite bound to that user; accepting adds a passkey to them (no username asked) and signs them in.
11. **UI: the role decides menus and pages.** `/api/auth/me` returns `role`, `user_id`, `has_password`. Middleware:
    admin-only pages (settings sections for providers/prompts/template/index, audit/costs, users) → 404 for others;
    menus hide what the role can't use; Account settings hide password and 2FA for passkey-only users.
12. **UI: read-only courses and draft for viewers (and for editors on others' published courses).** No in-place
    block editing, assistant, import, compile, publish or share controls; others' courses show the owner's name.
13. **UI: read-only lessons for viewers.** No notes, ink, «non capita», page add/delete, generate, sharing.
14. **UI: read-only labs for viewers.** No file edits, notes, comments, lab assistant.
15. **Draft: comments and suggestions on published courses (backend).** Table `draft_notes` (`course_id`,
    `chapter_id`, `author_id`, `kind` comment|suggestion, anchor: `src` of the block as shown + `start`/`end`, `body`,
    `proposed` LaTeX for suggestions, `status` open|accepted|rejected|resolved, `resolved_by`, timestamps).
    Create only on published courses the user can read (any role); the author edits/deletes while open; owner/admin
    list all, others only their own; accept = the block write of `preview.py` (409 if the block changed since);
    re-anchoring finds the stored `src` in the current chapter, else the note is flagged «testo cambiato».
16. **Draft: a reader writes comments and suggestions on a block of a published course.** Select a block →
    «Commenta» / «Suggerisci modifica» (the same text box as the double-click edit); markers on their own notes.
17. **Draft: the author sees the notes on their text and accepts, rejects or resolves them.** Markers on blocks with
    open notes, a list per chapter (with «testo cambiato» ones), accept/reject/resolve, open-notes count on the
    course in the dashboard.

## Not in this plan (ideas for the board, 💡 Idea, P2)

* Per-user «non capita» marks and last open page of a lesson.
* AI budget per user.
* Editors sharing unpublished courses with each other.
* Handing a disabled user's courses to someone else.
* Replies/threads on draft notes, and a notification when a note arrives.

## Board

Parent issue #69 «Auth: più utenti, su invito con passkey e ruoli». Steps 1–17 are its sub-issues #70–#86, in order
(step *n* is #(69+*n*)); the five ideas above are on the board in 💡 Idea, P2.
