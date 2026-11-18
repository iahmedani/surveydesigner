---
name: feature
description: End-to-end workflow for adding a feature or fixing a bug in Survey Designer — frame, plan, implement backend/frontend with the project agents, test, review, update docs. Use when the user asks to build, add or change functionality.
---

# Feature workflow

Argument: a description of the feature or bug (`$ARGUMENTS`).

1. **Frame.** Read `docs/kb/feature-playbook.md` and the KB doc(s) for the area.
   Restate the feature as acceptance criteria, note who uses it (SPA user vs admin
   content manager), the org-scoping/permission rule, and whether it touches the survey
   engine. Ask the user only if a product decision is genuinely ambiguous.
2. **Branch.** If on `main`, create `feat/<short-slug>` (or `fix/<slug>`).
3. **Plan.** List the files to change (backend → API → frontend → i18n → tests → docs).
   Keep it the smallest change that meets the criteria.
4. **Implement.** Small changes: do them directly. Larger ones: delegate to the
   `backend-dev`, `survey-engine` and/or `frontend-dev` agents with the acceptance
   criteria, the files involved, and the API contract between backend and frontend
   (define the contract first so both sides can run in parallel).
5. **Verify.** Run targeted tests, then `scripts/check.sh quick`. Compare failures to
   the baseline in `docs/kb/dev-setup.md` — only *new* failures matter, and they must be fixed.
   Run the full backend suite if models, signals, permissions or the survey engine changed.
6. **Review.** Run the `code-reviewer` agent on the diff and fix Blockers/Should-fix items.
7. **Docs.** Run the `kb-sync` steps for anything whose behaviour changed.
8. **Report.** Summarize what changed, how it was verified (commands + results), any
   migrations/env vars, and remaining risks. Don't commit or push unless the user asks.
