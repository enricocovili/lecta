# Lecta

Self-hosted platform for university notes, worked on with an AI assistant.

* **Lessons**: take notes during the lecture, Markdown next to every slide and pen strokes over it, on a tablet or a
  laptop; a lesson's **lab** holds the files explained in class, to read and comment.
* **Study text**: «Integra appunti» turns a lesson into the study text of its course, typeset by LaTeX. You read it as a
  draft, and an **AI assistant** that reads and changes everything in the course does the editing (one click undoes an
  answer). There is no LaTeX editor; the LaTeX project can be downloaded whenever you want.
* **Public site**: only the courses you switch ON, always in their latest version (PDF and LaTeX source).

One admin, everything in Docker, every setting in the web UI.

## Quick start

```sh
docker network create webnet   # only if it doesn't exist yet
docker compose up -d --build
docker compose logs backend | grep "setup code"
```

Point your HTTPS reverse proxy at `http://lecta-frontend:4321` on `webnet`, open the site and follow the setup wizard.
Details: [docs/DEPLOY.md](docs/DEPLOY.md).

## Documentation

| | |
|-|-|
| [DEPLOY.md](docs/DEPLOY.md) | running, first setup, AI providers, configuration, backup and restore, recovery |
| [DEVELOPMENT.md](docs/DEVELOPMENT.md) | tests, repository layout, the planning board |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | services and networks, security boundaries, LaTeX sandbox, jobs |
| [WORKSPACE.md](docs/WORKSPACE.md) | courses, the draft, the assistant, publishing, search and export |
| [LESSONS.md](docs/LESSONS.md) · [LABS.md](docs/LABS.md) | lessons and their labs |
| [IMPORT.md](docs/IMPORT.md) | from a lesson to the study text: extraction, reading, writing, placement |
| [AI-WORKSPACE.md](docs/AI-WORKSPACE.md) | API contract of the draft and the assistant |
| [STATUS.md](docs/STATUS.md) · [DECISIONS.md](docs/DECISIONS.md) · [PLAN.md](docs/PLAN.md) | where things stand, choices made, the original plan |

Open work, ideas and bugs: the [Features board](https://github.com/users/enricocovili/projects/1).
