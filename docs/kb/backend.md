# Backend (`dj-be/`)

Django 5.2.11 · DRF 3.16 · drf-spectacular · django-rq · django-redis · pyxform 4.5.0 ·
Authlib (OIDC) · django-storages (S3) · whitenoise · Sentry. ASGI via gunicorn+uvicorn.

## Conventions you must know

- **One settings module**: `survey_designer/settings.py`, env-driven via `.env`
  (`load_dotenv()`). Required: `SECRET_KEY`, `ALLOWED_HOSTS` (`;`-sep), `POSTGRES_*`.
  List-type env vars are `;`-separated.
- **Bare app imports**: settings puts `survey_designer/apps` on `sys.path`, so code
  imports `from modules.models import Module`. Use that style. (A few files use
  `survey_designer.apps.X` — avoid; it can double-import modules.)
- **Model mixins** (`apps/core/models.py`): `TimestampMixin`, `SoftDeleteMixin`
  (`is_active`), `UserTrackingMixin`, `BaseWFPModelMixin` (unique case-insensitive
  `name`, `label`, `description`), `OrganizationsModelMixin` (M2M organizations),
  `BaseTranslationMixin`, `OrderFieldMixin` (order = max+1). New content models
  should compose these.
- **Postgres only** (ArrayField, ArrayAgg, ICU `case_insensitive` collation).
- **Org scoping is mandatory** for content queries: use
  `core.organization_scope.filter_for_selected_organizations()` /
  `validate_scoped_ids()`; mutations go through `organization.permissions`.
- Style: black + isort + flake8, line length 88 (`setup.cfg`). `make fmt` fixes.

## Apps

| App | Purpose | Key files |
|---|---|---|
| `accounts` | Custom `User` (email login, org auto-assigned from email domain), groups/roles, publishing API sites & encrypted API keys | `models.py`, `const.py` (`PermissionGroups`), `views.py`, `management/commands/init_users.py`, `update_group_permissions.py` |
| `core` | Mixins, org middleware/scope, OIDC auth, permissions, email, S3 storages, shared admin mixins, seed commands | `models.py`, `middleware.py`, `organization_scope.py`, `auth/`, `permissions.py`, `send_email.py`, `tasks.py` |
| `organization` | `Organization` (name, `allowed_domains` ArrayField) + object permission rules | `permissions.py` (`can_mutate_object`, …), `utils.get_organizations()`, `mixins.py` |
| `surveys` | Survey taxonomy: `SurveyCategory`, `SurveyType` (`password_protected`), `SurveyMode`, `SurveyAttribute` (UI: "Survey Context") + translations; `Survey` (preview XML file) | `models.py`, `views.py` |
| `modules` | `Module` → `Submodule`, `SubmoduleMapping` (which category/type/mode/attribute a submodule applies to, `is_mandatory`), `IndicatorArea`, `Indicator`, `IndicatorMapping`. **Also hosts the generate/preview/upload/validate API.** | `models.py`, `views.py` (large), `serializers.py`, `services.py` (`SubmoduleCompositionValidator`), `signals.py` |
| `questions` | Question bank: `BaseQuestion` hub, `RootQuestion`, `SubQuestion`, `Suffix`, `RecallPeriod`, `ChoiceGroup`, `ChoiceGroupFile` (CSV), `Choice`, `Calculation`, `RepeatSection` + translations; `SubmoduleQuestionConfig` (per-submodule copy of a question's config, see below). **XLSForm engine & validators.** | `models.py`, `signals.py`, `services/*`, `forms.py`, `views.py` (admin tools) |
| `saved_surveys` | `SavedSurvey` (a user's selection, uuid-addressed, shareable), `SubQuestionSubmodule`, `SubModuleOrder` | `views.py`, `serializers.py` (read/write split) |
| `change_requests` | Upload an .xlsx change proposal → approvers dry-run and apply it via `DataImport` | `views.py` (plain Django views under `/admin/change-requests/`) |
| `documents` | `Document` — stored generated .docx files | admin only |
| `frontend_content` | `FrontendContent` (key → markdown, severity, active): FAQ, tooltips, maintenance banner | `/api/frontend-content/`, `init_frontend_content` |

### Questions — behaviour hidden in signals (`apps/questions/signals.py`)
- Creating a RootQuestion/SubQuestion/RepeatSection auto-creates its `BaseQuestion`
  (`order = id`).
- Saving a root re-saves its sub-questions (sub-question **names are derived**:
  root + suffix + suffix_2 + recall period; type/choices inherited suffix_2 → suffix → root).
- **Renaming a question rewrites `${old}` → `${new}`** across dependent expressions,
  labels and translations.
- `*_dependencies` M2Ms (migration 0050) are kept in sync by the expression linter.
- The rename rewrite also covers `SubmoduleQuestionConfig` expression/label fields and
  both config translation models (found by `${old}` text containment, `bulk_update`,
  then cache invalidation on commit).
- Saving/deleting a config or its translations calls `modules.signals.invalidate_module_cache_on_commit()` (deferred to commit, once per transaction).

### Per-submodule question configuration (`SubmoduleQuestionConfig`)
A question keeps one global `name` and `type`, but a submodule can hold its own copy of
everything else (label, hint, logic fields, choice list/file, translations).
Design and rules: [design/submodule-question-config.md](design/submodule-question-config.md).
- **Full copy on create**: `SubmoduleQuestionConfig.create_from_question(submodule,
  base_question, user)` (uses `fill_from_question()`); afterwards independent. Delete = revert.
- `clean()`: question must belong to the submodule (via its root's `submodule` M2M);
  repeat sections can't be configured; choice/type rules exactly as `RootQuestion.clean()`
  (no extra rule for non-select types). `choices`/`choices_file` are `PROTECT`.
- Admin (`questions/admin.py`): add view asks only submodule + question, then redirects to
  the change view; change form lints expressions *as the shared question*. Entry links on
  the Submodule, RootQuestion and SubQuestion admin pages.
- Permissions: `organization.utils.get_organizations(config)` = the submodule's orgs, so
  only users who may mutate the submodule may add/change/delete in admin. Admins group gets
  full perms, Read Only gets view (`core/permissions.py`). A ChoiceGroup/ChoiceGroupFile's
  organizations include those of submodules whose configs use it.
- Bulk import / change requests: the `submodule_question_configs` sheet is authorised like
  the survey sheet (module must belong exactly to the declared organizations), not by
  `can_mutate_object`. A config row can't reference a question created or attached by the
  same workbook, so change requests need two rounds.
- Cache: config/translation signals call `modules.signals.invalidate_module_cache_on_commit()`,
  which runs on commit, once per transaction. The `${ref}` rename rewrite uses the same helper.
  In tests, wrap changes in `django_capture_on_commit_callbacks(execute=True)`.
- Resolution for generation/API lives in `questions/services/question_config.py`
  (see [survey-generation.md](survey-generation.md)).

## API routes

All under `/api/` (namespace `v1`), `IsAuthenticated` unless noted. Schema:
`/api/schema/`, Swagger `/api/docs/`.

| Route | Methods | Notes |
|---|---|---|
| `accounts/user/` | GET | current user, roles, `sites`, `can_access_cms` |
| `accounts/api-token-auth/` | POST | DRF token |
| `accounts/api-sites/` | GET | Kobo/ONA/MoDa sites |
| `accounts/api-keys/` | CRUD | user's encrypted publishing keys |
| `accounts/projects/?site=<key id>` | GET | proxies remote projects list |
| `organizations/` | GET | all orgs |
| `surveys/` | GET | `{categories, modes}` for the org scope |
| `modules/?type&mode&attributes` | GET | cached 1h (`key_prefix="modules_"`) |
| `submodules/?submodule_ids&indicator_ids` | GET | with questions (effective per-submodule config); cached 1h (`key_prefix="modules_submodules_"`) |
| `indicators/?type&mode&attributes` | GET | cached 1h (`key_prefix="modules_indicators_"`) |
| `generate/` | POST | → .xlsx / .zip, or 400/503 `ValidationResult` |
| `generate-doc/` | POST / GET `?jobId[&cancelJob=true]` | async Word codebook (RQ `generate-doc`) |
| `preview/` | POST | stores XML → Enketo URL (`url`, `enketo_url`) |
| `upload/` | POST | publish to Kobo / ONA / MoDa |
| `validate/` | POST | validation only |
| `order-validation/` | GET | selected-scope composition validation |
| `saved-surveys/` (+ `<uuid>/`, `<uuid>/copy/`) | CRUD | list = owner's only; retrieve/copy by uuid (sharing) |
| `frontend-content/` | GET | CMS markdown blocks |
| `*-autocomplete/` | GET | admin autocompletes |

Non-API: `/auth/login|callback|logout/`, `/auth/e2e-login/` (only if E2E enabled),
`/admin/` (+ `survey-health/`, `questions/import/`, `questions/constraint|relevant/`,
`change-requests/…`), `/health/`.

## Auth & permissions

- **OIDC** (`apps/core/auth/`): `/auth/login/` (PKCE) → IdP → `/auth/callback/` →
  `OIDCAuthenticationBackend` (Keycloak) or `…CIAM` (`IDENTITY_PROVIDER=CIAM`) →
  user get-or-create by email; new users join **Read Only** and the Notifications
  group is emailed. Redirects to `FRONTEND_BASE_DOMAIN`. `ModelBackend` remains as fallback.
- DRF auth: Session, Basic, Token. Default permission `SDModelPermissions`, but most
  API views use `IsAuthenticated` — **model perms effectively apply in admin only**.
- **Roles** (`accounts/const.PermissionGroups`): Admins (org admin), Global Admins,
  Bulk Upload, Change Requests, Notifications, Read Only. Group perms set by
  `update_group_permissions` from `core.permissions.AdminPermissions/ReadOnlyPermissions`.
- **Object rule**: org Admins may mutate only content assigned *exclusively* to their
  org; Global Admins/superusers anything (`organization/permissions.py`,
  enforced in `User.has_perm`).

## Caching, async, storage, email

- Redis cache only if `REDIS_URL` set (else LocMem). `modules/signals.py` calls
  `cache.delete_pattern("*modules_*")` on Module/Submodule/SubmoduleMapping and
  SubmoduleQuestionConfig changes (**django-redis only** — LocMem raises). All three
  cached endpoints use a `modules_*` key prefix. Edits to shared questions, choices and
  translations still don't invalidate (see production-readiness).
- RQ queues: `generic`, `generate-doc`, `send-email` (`make worker`).
- `UPLOAD_TO_S3=true` → `core.storage_backends.PublicMediaStorage`; else local `MEDIA_ROOT`.
- Email: `core.send_email.EmailHelper` via RQ, templates in `templates/email/`.

## Management commands (run by `make migrate`)

`init_users`, `generate_data`, `init_e2e_data` (if `E2E_INIT_DATA=true`),
`fix_module_ordering`, `init_frontend_content`, `update_group_permissions`,
`normalise_ordering`. Also `generate_questions`.
