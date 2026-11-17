# Survey Designer — Developer Knowledge Base

Engineering docs for people (and AI agents) changing this codebase. The
project-governance docs (CONTRIBUTING, GOVERNANCE, …) live one level up in `docs/`.

| Doc | Read it when… |
|---|---|
| [architecture.md](architecture.md) | You're new — the 10-minute mental model |
| [dev-setup.md](dev-setup.md) | Getting a working local env (Docker **or** no-Docker) |
| [backend.md](backend.md) | Touching `dj-be/` — apps, models, API routes, auth, caching |
| [frontend.md](frontend.md) | Touching `react-ui/` — routes, Redux, API calls, i18n |
| [survey-generation.md](survey-generation.md) | Touching XLSForm export, validation, preview, publishing |
| [testing.md](testing.md) | Writing or running tests (pytest, vitest, Playwright) |
| [feature-playbook.md](feature-playbook.md) | Adding a feature end-to-end — the checklist |
| [production-readiness.md](production-readiness.md) | Before shipping; known bugs & tech debt, prioritized |

## Keeping this current

- Docs describe **what the code does today**, with file paths. If you change
  behaviour described here, update the doc in the same PR (`/kb-sync` does this).
- Paths are relative to the repo root unless stated. Backend app paths are
  abbreviated as `apps/<app>/…` = `dj-be/survey_designer/apps/<app>/…`.
- Last full review: 2026-11-17 (at commit `01220da`).
