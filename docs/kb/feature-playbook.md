# Feature playbook

A short, repeatable path from idea to mergeable PR. Keep changes small; one feature
per branch.

## 0. Frame it (5 minutes)
- Who uses it — survey builder (SPA) or content manager (admin)?
- Which org-scoping rule applies? Who may read / mutate?
- Does it touch generation/validation? If so, read [survey-generation.md](survey-generation.md) first.
- Write acceptance criteria as testable statements.

## 1. Backend
1. **Model** (if needed) in the owning app; compose `core.models` mixins
   (`OrganizationsModelMixin`, `SoftDeleteMixin`, `TimestampMixin`, …).
   Add `*Translation` model if user-visible text is multilingual.
2. `poetry run python manage.py makemigrations <app>` — review the migration;
   data migrations must be reversible or explicitly `RunPython.noop` backwards.
3. **Admin** registration if content managers edit it — reuse
   `organization.mixins` / `core.admin` mixins so org permissions apply.
   If Admin/Read Only groups need the new perms, extend
   `core.permissions.AdminPermissions/ReadOnlyPermissions` (applied by
   `update_group_permissions`).
4. **API**: serializer + view in the app, route in the app's `urls.py`.
   `permission_classes = [IsAuthenticated]` at minimum; scope querysets with
   `filter_for_selected_organizations()`; validate incoming ids with
   `validate_scoped_ids()`. Annotate with drf-spectacular if non-trivial.
5. **Caching**: if you change data served by cached endpoints (`modules/`,
   `submodules/`, `indicators/`), make sure the cache is invalidated.
6. **Async**: anything slow (>2s) goes to an RQ queue (`django_rq.enqueue`, queue
   `generic`), pass ids not model instances.
7. Tests (see [testing.md](testing.md) table).

## 2. Frontend
1. Types in `app/types/api.d.ts`.
2. Thunk in `redux/actions/<x>Actions.tsx` using `API` + `createAppAsyncThunk`;
   slice in `redux/reducers/` with `{isLoading, error, data}`; register in
   `reducers/index.tsx`.
3. Component in `app/components/<Name>/index.tsx` using `@wfp/react` components.
   Route-level pages in `app/views/` + route in `app/index.tsx` (wrap in `PrivateRoute`).
4. Strings: add keys to **all six** `locales/*/translations.json`.
5. Errors: surface via the `notification` slice; parse with `utils/apiError.tsx`.
6. Tests with Testing Library; extend Playwright if the wizard changes.

## 3. Finish
- `scripts/check.sh` — no *new* failures vs. the baseline in [dev-setup.md](dev-setup.md).
- Update `docs/kb/` if you changed behaviour described there (`/kb-sync`).
- Run the `code-reviewer` agent (or `/code-review`) on the diff.
- PR description: what/why, screenshots for UI, migration notes, env vars added
  (also add them to `.env.sample`, `.env.local`, `docker-compose.yml` `environment:`).
