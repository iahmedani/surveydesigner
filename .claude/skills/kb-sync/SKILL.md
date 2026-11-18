---
name: kb-sync
description: Update the docs/kb knowledge base (and CLAUDE.md) so it matches the code after a change. Use after implementing a feature, after merging upstream changes, or when the user says the docs are out of date.
---

# KB sync

1. Find what changed: `git diff main...HEAD --stat` plus uncommitted `git status`
   (or the commit range in `$ARGUMENTS`).
2. Map changes to docs:
   - models/apps/API routes/auth/caching/commands → `docs/kb/backend.md`
   - routes/slices/API calls/i18n → `docs/kb/frontend.md`
   - questions/services, generate/preview/upload, validation codes → `docs/kb/survey-generation.md`
   - tooling, env vars, setup, baseline counts → `docs/kb/dev-setup.md` (+ `CLAUDE.md`)
   - tests/fixtures → `docs/kb/testing.md`
   - fixed or newly found bugs/debt → `docs/kb/production-readiness.md`
3. Edit only the affected sections. Describe what the code does now, with file paths;
   verify every claim against the code (grep/read) — never document from memory.
   Remove statements that are no longer true; tick off fixed known issues.
4. Bump "Last full review" in `docs/kb/README.md` only after a full review, not a partial sync.
5. Report which docs changed and why.
