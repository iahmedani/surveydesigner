# Survey Designer

WFP tool for building standardized XLSForm (ODK/Kobo/ONA/MoDa) surveys from a curated
question bank. Django 5.2 + DRF backend (`dj-be/`), React 18 + TS + Vite SPA (`react-ui/`).

**Knowledge base: `docs/kb/`** — start at `docs/kb/README.md`. Read the relevant
doc before changing an area; update it in the same change if behaviour changes.

## Environment (this machine: no Docker)
```bash
export PATH=$HOME/.local/bin:$PATH     # poetry, pnpm
scripts/dev-services.sh start          # Postgres 16 (ICU) + Redis
```
`poetry`/`make` run in `dj-be/`; `pnpm` runs in `react-ui/`. Setup details: `docs/kb/dev-setup.md`.

## Commands
- All gates: `scripts/check.sh [all|quick|backend|frontend]`
- Backend tests: `cd dj-be && poetry run pytest` (full ≈6 min). Single test:
  `poetry run pytest -o addopts="" survey_designer/apps/<app>/tests/test_x.py::name`
- Backend format: `poetry run make fmt`; migrations: `poetry run python manage.py makemigrations <app>`
- Frontend: `pnpm test`, `pnpm tsc`, `pnpm lint`, `pnpm dev`

Known baseline failures (don't add to them): 74 `tsc` errors, 127 ESLint errors,
1 isort file (`core/auth/views.py`). Backend tests: 767 pass.

## Rules
- **Org scoping**: every content query/API goes through
  `core.organization_scope` (`filter_for_selected_organizations`, `validate_scoped_ids`);
  mutations through `organization.permissions`. API tests must send
  `HTTP_SURVEY_DESIGNER_ORGANIZATIONS`. Missing header = empty data, not an error.
- **Generation**: generate/preview/upload must go through
  `modules.views.prepare_validated_artifact()`. pyxform is pinned in both
  `pyproject.toml` and `questions/services/form_validation.PYXFORM_VERSION`.
- Backend imports use bare app names (`from modules.models import …`).
- Reuse `core.models` mixins for new models; add migrations; never edit applied migrations.
- Questions have side-effecting signals (auto BaseQuestion, name derivation,
  `${ref}` rewriting on rename) — see `docs/kb/backend.md`.
- Frontend: use the `API` axios client, `@wfp/react` components, RTK thunks; add i18n
  keys to all 6 locales in `react-ui/src/locales/`.
- Slow work → RQ queue; pass ids, not model instances.
- New env vars → `.env.sample`, `.env.local`, `docker-compose.yml`.
- Never commit `.env` files or secrets.

## Agents & skills (`.claude/`)
Agents: `backend-dev`, `frontend-dev`, `survey-engine` (XLSForm/validation),
`code-reviewer` (production-readiness review).
Skills: `/feature` (end-to-end feature workflow), `/check` (run gates vs baseline),
`/kb-sync` (update docs after changes).
