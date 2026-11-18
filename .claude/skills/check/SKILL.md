---
name: check
description: Run Survey Designer's quality gates (lint, types, migrations check, tests) and compare against the known baseline. Use before committing, before a PR, or when the user asks whether things pass.
---

# Check

1. `export PATH=$HOME/.local/bin:$PATH` and run `scripts/check.sh ${ARGUMENTS:-quick}`
   from the repo root (`quick` skips the ~6 min full pytest; use `all` before a PR or
   when models/signals/permissions/survey engine changed).
2. Compare with the baseline in `docs/kb/dev-setup.md` ("Baseline" section):
   - tsc errors: `cd react-ui && pnpm -s tsc | grep -c 'error TS'` (baseline 74)
   - ESLint: last line of `pnpm -s lint` (baseline 127 errors / 64 warnings)
   - isort: 1 file, `core/auth/views.py` (baseline)
3. Report a short table: gate → pass/fail → new vs. baseline. For any **new** failure,
   show the relevant lines and the file it comes from. If counts dropped, say so and
   suggest updating the baseline numbers in `docs/kb/dev-setup.md` and `CLAUDE.md`.
