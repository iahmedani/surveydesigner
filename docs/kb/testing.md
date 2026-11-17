# Testing

## Backend — pytest (`dj-be/`)
- Tests: `survey_designer/apps/<app>/tests/test_*.py` (~690 tests). Bulk-upload
  scenarios: `apps/questions/tests/bulk_upload/` (own `conftest.py`), fixture
  workbook `apps/questions/tests/files/questions.xlsx`.
- Config `setup.cfg`: `-n auto` (xdist), coverage on `survey_designer`,
  **`--cov-fail-under 70`**, junit `test-results.xml`.
- Needs real **Postgres with ICU** + **Redis** (`delete_pattern` in cache signals).
  No SQLite path, no separate test settings.
- Global fixtures in `survey_designer/conftest.py` (DB access is autouse):
  - users/clients: `user`, `admin`, `global_admin`, `api_client`,
    `api_client_authenticated(_admin)`, `logged_client`, `logged_admin_client`,
    `logged_global_admin_client`
  - content: `organization_1/2`, `module_1/2`, `survey_category/type/mode/attribute_*`,
    `submodule_mapping_*`, `submodule_1..12`, `root_question_1..5`, `sub_question_1..4`,
    `choices_1/2`, `suffix_1/2`, `recall_period_1`, `repeat_section_1/2`,
    `indicator_1/2`, `calculation_1`, `xls_form_data`, `saved_survey_1`, `change_request_1`
  - helper `create_submodule_with_mapping()`
- factory_boy: `accounts/factories.py`, `modules/factories.py`, `questions/factories.py`,
  `organization/tests/factories.py`. Prefer fixtures/factories over ad-hoc objects.
- **API tests must send the org header**, e.g.
  `client.get(url, HTTP_SURVEY_DESIGNER_ORGANIZATIONS=str(org.id))` — otherwise
  scoped endpoints return empty data and the test passes for the wrong reason.

```bash
cd dj-be
poetry run pytest                                     # full (≈6 min, parallel)
poetry run pytest -o addopts="" survey_designer/apps/modules/tests/test_views.py -k generate
poetry run make test                                  # lint + makemigrations --check + pytest
```
- `-o addopts=""` drops setup.cfg's `-n auto`/coverage flags for quick targeted runs
  (`-p no:xdist --no-cov` does **not** work with these addopts).
- **Two runs at once** (e.g. parallel agents) collide on `test_app_db`: give each its own
  DB with `POSTGRES_DB=sd_<name> poetry run pytest …`.

## Frontend — vitest (`react-ui/`)
- Tests beside components (`*.test.tsx` + `__snapshots__`), logic tests for
  `ordering.ts`, `selection.ts`, `moduleCriteria.ts`, `utils/apiError`, `utils/generate`.
- Use `createTestStore()` (from `redux/store.tsx`) and `vi.mock` for API.
- `pnpm test` (watch), `pnpm test:ci` (coverage), update snapshots with `pnpm test -u`
  **only when the UI change is intentional**.
- Note: `tsconfig` excludes tests, so `pnpm tsc` doesn't type-check them.

## E2E — Playwright (`react-ui/e2e/`)
Real stack, no mocks. Specs: `smoke`, `happy-flow` (create/edit/share/copy/preview/
export), `preservation` (ordering & sub-question choices survive save).
Auth uses `POST /auth/e2e-login/` with `X-E2E-Auth-Token`; backend needs
`ENABLE_E2E_AUTH=true`, `ENV=ci|test`, `E2E_AUTH_TOKEN`, and `E2E_INIT_DATA=true` seed.
```bash
cd dj-be && docker compose --env-file .env.e2e up -d --build
cd ../react-ui && pnpm exec playwright install chromium && pnpm e2e:ci
```
Without Docker: start services, set the E2E env vars in `dj-be/.env`, run
`make migrate` + `make run_dev`, then `pnpm e2e:ci` (Playwright starts `pnpm dev`).

## What to test for a change
| Change | Minimum tests |
|---|---|
| New/changed API endpoint | auth required, org-scoped (with & without header, other org's ids rejected), happy path, validation errors |
| Model change | migration present (`makemigrations --check`), admin add/change, permissions for org Admin vs Global Admin |
| XLSForm / validation | `test_xls_form.py` / `test_form_validation.py` asserting rows or issue codes |
| Frontend component | behaviour test with Testing Library; snapshot only as a supplement |
| Wizard flow change | extend the relevant Playwright spec |
