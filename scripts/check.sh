#!/usr/bin/env bash
# Run the project's quality gates. Usage: scripts/check.sh [all|backend|frontend|quick]
#   quick = backend lint + migrations check + frontend tsc/lint/unit (no full pytest)
# Prints a summary table; exit code != 0 if any step failed.
# NOTE: some steps fail on a known baseline — see docs/kb/dev-setup.md#baseline.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$HOME/.local/bin:$PATH"
MODE="${1:-all}"
declare -a RESULTS=()
FAIL=0

run() { # run <label> <dir> <cmd...>
  local label="$1" dir="$2"; shift 2
  echo -e "\n=== $label ==="
  if (cd "$dir" && "$@"); then RESULTS+=("PASS  $label"); else RESULTS+=("FAIL  $label"); FAIL=1; fi
}

backend() {
  "$ROOT/scripts/dev-services.sh" status | grep -q "postgres: up" || "$ROOT/scripts/dev-services.sh" start || true
  run "backend: flake8"         "$ROOT/dj-be" poetry run flake8 --exclude migrations survey_designer
  run "backend: black"          "$ROOT/dj-be" poetry run black --check -q --exclude migrations survey_designer
  run "backend: isort"          "$ROOT/dj-be" poetry run isort --check-only -q survey_designer
  run "backend: migrations"     "$ROOT/dj-be" poetry run python manage.py makemigrations --check --dry-run
  [ "$MODE" = quick ] || run "backend: pytest" "$ROOT/dj-be" poetry run pytest -q -p no:cacheprovider
}

frontend() {
  run "frontend: tsc"           "$ROOT/react-ui" pnpm -s tsc
  run "frontend: eslint"        "$ROOT/react-ui" pnpm -s lint
  run "frontend: vitest"        "$ROOT/react-ui" pnpm -s vitest run
}

case "$MODE" in
  all|quick) backend; frontend ;;
  backend) backend ;;
  frontend) frontend ;;
  *) echo "usage: $0 [all|backend|frontend|quick]"; exit 2 ;;
esac

echo -e "\n=== Summary ($MODE) ==="
printf '%s\n' "${RESULTS[@]}"
exit $FAIL
