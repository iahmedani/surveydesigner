# Local development setup

Toolchain: **Python 3.11 + Poetry**, **Node 20 + pnpm 10.8.0**, **PostgreSQL 16
built with ICU**, **Redis**. Commands run from the sub-project directory:
`poetry`/`make` in `dj-be/`, `pnpm` in `react-ui/`.

## Option A — Docker (full stack)

```bash
cd dj-be && cp .env.local .env    # .env.local is pre-wired for the compose stack
docker compose up --build         # postgres, redis, minio, maildev, keycloak, initdb, api, worker
cd ../react-ui && cp .env.sample .env && pnpm install && pnpm dev   # http://localhost:3000
```
API on :8080, Keycloak on :8081 (admin/admin), MinIO console :9001, maildev :1080.
Run backend tests in the container: `docker compose run --rm api test`
(the image's entrypoint is `make`).

## Option B — No Docker (SageMaker / sandboxes)

Used on this machine because there is no Docker daemon.

```bash
# one-time
uv python install 3.11 && uv tool install poetry==1.8.5
cd dj-be && poetry env use "$(uv python find 3.11)" && poetry install --no-root
# Postgres 16 WITH ICU, extracted under $HOME (SageMaker restarts wipe /usr, so
# a plain `apt-get install` disappears). Needs apt sources, no root to run.
mkdir -p ~/.local/pg16-deb && cd ~/.local/pg16-deb && sudo apt-get update -qq
apt-get download postgresql-16 postgresql-client-16 libicu74 libxml2 libpq5
for d in *.deb; do dpkg -x "$d" ~/.local/pg16; done && cd -
conda create -y -p ~/.local/sd-services -c conda-forge redis-server   # or apt redis-server
corepack enable --install-directory ~/.local/bin && corepack prepare pnpm@10.8.0 --activate
cd ../react-ui && pnpm install

# every session
export PATH=$HOME/.local/bin:$PATH
scripts/dev-services.sh start         # postgres :5432 (postgres/r00t, db app_db), redis :6379
```

`dj-be/.env` for this mode = `.env.sample` with:
`POSTGRES_HOST=127.0.0.1`, `REDIS_URL=redis://127.0.0.1:6379/0`, `UPLOAD_TO_S3=False`,
`ENV=local`, `EMAIL_HOST=localhost`,
`DJANGO_EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend`.

> ⚠️ **conda-forge PostgreSQL is built `--without-icu`** → every test fails with
> `NotSupportedError: ICU is not supported in this build` (the `accounts` migrations
> create a `case_insensitive` ICU collation). Use the Ubuntu/official build.
> `scripts/dev-services.sh` looks for `$SD_PG_BIN`, then `~/.local/pg16/usr/lib/postgresql/16/bin`
> (setting `LD_LIBRARY_PATH` for its bundled libs), then `/usr/lib/postgresql/16/bin`.
> If start fails after a restart with "another server might be running", delete
> `~/.local/sd-data/pg/postmaster.pid`.

Run the app:
```bash
cd dj-be && poetry run make migrate     # migrations + seed data (users, demo content, frontend content)
poetry run make run_dev                 # runserver on :8080
poetry run make worker                  # RQ workers (Word export, emails) — optional
```
Seeded users (`init_users`, local only!): `admin@wfp.org`, `user@wfp.org`, `me@me`,
password `123`. Without an IdP, log into `/admin/` with those (ModelBackend
fallback) — the SPA login needs OIDC or the E2E login endpoint.

## Everyday commands

| Task | Command |
|---|---|
| All checks (what CI should run) | `scripts/check.sh` (or `backend` / `frontend`) |
| Backend tests | `cd dj-be && poetry run pytest` (xdist, coverage gate 70%) |
| One test | `poetry run pytest -o addopts="" apps/…/test_x.py::test_name` — run from `dj-be/`, paths under `survey_designer/` |
| Backend lint / format | `poetry run make lint` / `poetry run make fmt` |
| Migrations | `poetry run python manage.py makemigrations <app>`; check: `makemigrations --check --dry-run` |
| Frontend dev | `cd react-ui && pnpm dev` |
| Frontend tests | `pnpm test` (watch) / `pnpm test:ci` |
| Types / lint | `pnpm tsc` / `pnpm lint` |
| E2E | see [testing.md](testing.md) |
| API docs | http://localhost:8080/api/docs/ (Swagger), `/api/redoc/` |

## Baseline (2026-11-17, commit 01220da)

Known **pre-existing** failures — don't be surprised, don't make them worse:
- Backend: 767 tests pass, coverage 88% (updated on `feat/submodule-question-config`).
  `flake8` and `black` are clean; `isort` flags 1 file (`apps/core/auth/views.py`).
- Frontend: 57 vitest tests pass; `pnpm tsc` → 74 errors; `pnpm lint` → 127 errors / 64 warnings.

`scripts/check.sh` reports these as failures; compare counts against this baseline
rather than expecting green, until the debt is paid down.
