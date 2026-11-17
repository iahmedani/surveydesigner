# Architecture

## What the product does

Field staff (mainly WFP, but multi-organization) build **XLSForm surveys** for
ODK-based tools (MoDa, Kobo, ONA) by picking from a **curated, standardized
question bank** instead of writing forms by hand. Standard naming/labeling is
preserved, so data stays comparable across surveys.

Two audiences:
- **Survey builders** use the React app: define survey → pick modules/indicators →
  pick extra questions + languages → download XLSForm / Word codebook, or publish
  directly to Kobo/ONA/MoDa.
- **Content managers** (org Admins, Global Admins) curate the question bank in the
  **Django admin** ("CMS"), including bulk import, change requests and the survey
  health scanner.

## Components

```
 Browser ──(session cookie + CSRF, header Survey-Designer-Organizations)──┐
   │                                                                      ▼
 react-ui/ (Vite SPA, :3000)             dj-be/ (Django 5.2 + DRF, ASGI/gunicorn, :8080)
   - @wfp/react UI kit                     /api/*      JSON API (DRF)
   - Redux Toolkit                         /admin/*    Django admin = content CMS
   - i18next (6 locales)                   /auth/*     OIDC login (Keycloak or WFP CIAM)
                                           /health/    django-health-check
                                              │
              ┌───────────────┬───────────────┼───────────────┬──────────────┐
          PostgreSQL 16     Redis           RQ workers       S3 / MinIO     Keycloak/CIAM
          (ICU collation)   cache + queues  generic,         media,         (OIDC IdP)
                                            generate-doc,    previews,
                                            send-email       choice CSVs
                                              │
                         External: Enketo (preview), Kobo / ONA / MoDa (publish)
```

- The frontend is a **separately deployed static build** (`react-ui/dist/`). It is
  *not* served by Django (old README sections claiming otherwise are stale).
- Auth is **server-side**: the SPA links to `${VITE_APP_API_ENDPOINT}/auth/login/`;
  Django does OIDC + PKCE, sets a session, then redirects to `FRONTEND_BASE_DOMAIN`.
  The SPA has no OIDC library.
- **Multi-tenancy** is by organization. Every content query is scoped by the
  `Survey-Designer-Organizations` request header (comma-separated org ids), parsed
  by `apps/core/middleware.py` and applied via `apps/core/organization_scope.py`.
  **No header → empty results, not an error.**

## Core domain model (simplified)

```
SurveyCategory ─< SurveyType ── SurveyMode, SurveyAttribute ("context")
        │ (filters via SubmoduleMapping / IndicatorMapping, with is_mandatory flags)
Module ─< Submodule >─< RootQuestion ─< SubQuestion (root + suffix [+ suffix_2] [+ recall period])
                              │              
                        ChoiceGroup / ChoiceGroupFile(CSV) ─< Choice
IndicatorArea ─< Indicator >─< BaseQuestion (polymorphic hub: root | sub | repeat section)
SavedSurvey = a user's selection (orgs, type/mode, submodules, indicators, sub-questions, ordering, languages)
```

Every content model has `organizations` (M2M), soft delete (`is_active`), and
`*Translation` rows per language.

## The main request: generate a survey

`POST /api/generate/` → `apps/modules/views.py:prepare_validated_artifact()` →
`questions/services/xls_form.py:XLSForm` builds the workbook → `form_validation.py`
runs codebook checks, pyxform 4.5.0 conversion and XML/XPath compatibility checks →
returns `.xlsx` (or `.zip` with external choice CSVs), or a 400/503 `ValidationResult`.
Preview and publish reuse **the same validated artifact**. Details:
[survey-generation.md](survey-generation.md).

## Repo layout

```
dj-be/            Django backend (poetry, Makefile, Dockerfile, docker-compose.yml)
  survey_designer/settings.py   single settings module, env-driven (python-dotenv)
  survey_designer/apps/<app>/   apps importable by bare name (sys.path hack)
  survey_designer/templates/    admin overrides, emails
react-ui/         Vite + React 18 + TS SPA, Playwright e2e in react-ui/e2e/
docs/             governance docs; docs/kb/ = this knowledge base
scripts/          dev helpers (dev-services.sh, check.sh)
.claude/          Claude Code agents & skills for this repo
```
