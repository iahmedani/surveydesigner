# Production readiness

## Release checklist
- [ ] Backend tests pass, coverage ≥ 70%; `makemigrations --check` clean.
- [ ] No new lint/type errors vs. baseline ([dev-setup.md](dev-setup.md#baseline-2026-11-17-commit-01220da)).
- [ ] Migrations reviewed: safe on large tables, reversible, data migrations tested.
- [ ] New env vars documented in `.env.sample` + `docker-compose.yml`, with safe defaults.
- [ ] New endpoints: authenticated, org-scoped, no secrets in responses, no N+1
      (use `select_related`/`prefetch_related`; Postgres `statement_timeout` is 15s).
- [ ] Slow work runs on RQ, not in the request (gunicorn timeout 300s).
- [ ] Cache invalidation considered for anything served by cached endpoints.
- [ ] Generation changes: full `questions` + `modules` suites, manual preview in Enketo.
- [ ] User-facing strings translated in all locales.
- [ ] Prod settings: `DEBUG=false`, real `SECRET_KEY`, `SEND_TO_SENTRY=true`,
      `UPLOAD_TO_S3=true`, `ENABLE_E2E_AUTH` unset.
- [ ] Rollback plan (esp. for data migrations).

## Known issues (verified 2026-11-17)

Ordered by suggested priority. Each is a good, contained ticket.

### High
1. **Decrypted publishing API keys are returned by the API** —
   `apps/accounts/serializers.py` `raw_key = CharField(source="get_key")` is in list/retrieve
   responses. Prefer write-only + masked display. Also: key derivation uses
   `SECRET_KEY`, so rotating it makes all stored keys unreadable.
2. **Stale cache after question edits** — `/api/modules/`, `/api/submodules/` and
   `/api/indicators/` are cached 1h. Since 2026-11-17 all use a `modules_*` key prefix and
   are cleared on Module/Submodule/SubmoduleMapping/SubmoduleQuestionConfig changes, but
   **no signal fires on shared RootQuestion/SubQuestion/Choice/translation edits**, so
   those can take up to 1h to show in the wizard.
3. **`init_users` seeds known passwords** (`123`) and runs in `make migrate` in every
   environment (only when the users table is empty). Gate it to `ENV in (local, ci, test)`.
4. **No backend CI pipeline in the repo**, and pre-commit only covers the frontend.
5. **`make run` uses `gunicorn --reload`** (also used in production image).

### Medium
6. Change request notification gets `change_request=None`:
   `apps/change_requests/views.py:35` `self.object = form.save_m2m()` (returns None).
7. `SavedSurveyViewset.copy` uses `SavedSurvey.objects.get(uuid=…)` → 500 on unknown
   uuid (retrieve uses `get_object_or_404`). Retrieve/copy are open to any
   authenticated user with the uuid — intended for sharing, but worth a conscious decision.
8. Frontend `logout` dispatches `authActions.clearData` without `()`
   (`react-ui/src/app/redux/actions/authActions.tsx:10`).
9. Kobo URLs hardcoded (`apps/modules/views.py` ~L366/377) instead of using `site.url`.
10. Debug `print()` in `apps/core/auth/views.py:66-67`.
11. `delete_pattern` only exists on django-redis → cache signals raise without `REDIS_URL`.
12. If `EMAIL_HOST_USER` is set, settings force port 587 + TLS, ignoring `EMAIL_PORT`/`EMAIL_USE_TLS`.
13. OIDC: callback requires `session_state`; issuer is checked against `OIDC_TOKEN_ENDPOINT`.

### Low / cleanup
- Frontend baseline: 74 `tsc` errors, 127 ESLint errors. Fix before enabling in CI.
- `pnpm translate` / `dj-be/translationcli.py` broken (stale path, deps not declared).
- Stale README sections (translations workflow, Django serving the SPA).
- Frontend: nav login link uses client-side route `/auth/login/` (404s); Sentry DSN
  hardcoded; i18n `debug: true`; no RTL for Arabic; many hardcoded English strings.
- Frontend deps: vitest version conflict (0.34.6 vs 0.21.1), Testing Library 12 with
  React 18, unused deps (vite-plugin-pwa, moment, dotenv, ejs); test deps in `dependencies`.
- Backend: unused `lark` dep; `factory-boy`/`pytest-mock` in main deps; mixed import
  styles (`survey_designer.apps.X` vs bare); empty `questions/services/questions_import.py`
  shadowed by the package; `order-validation/` URL still named after the old
  `SubmodulesOrder`; dead `OIDC_AUTH` setting; `LOCALE_PATHS` dir missing; django-silk installed but disabled.
- `RootQuestion.duplicate()` scans all names in Python; `ChoiceGroupFile.clean()` iterates all files.
