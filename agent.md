# AGENTS.md — Standing rules for this repository

Place this file in the ROOT of the project (next to `requirements.txt`). Codex reads it automatically.

## Product
School ERP for online Islamic schools (live Quran/Arabic classes): attendance (core), students,
classes, lesson progress (Sabaq/Sabqi/Manzil), fees, reports, staff roles. Goal: a secure,
production-ready, multi-school SaaS product that can be sold and scaled.
Users are school admins and teachers; data includes **children's personal data** → privacy-critical.

## Current stack (v0.1 baseline)
Python 3.12, Flask 3, SQLite (raw parameterised SQL), Jinja templates, no inline JS/CSS (strict CSP),
pytest (30 tests). Layout: `schoolerp/{__init__,db,security,utils,config}.py`, `schoolerp/views/*.py`,
`schoolerp/templates/`, `schoolerp/static/`, `tests/`.
Stack changes require an ADR (see below) and my approval.

## Non-negotiable security rules
1. Never build SQL with string formatting/concatenation of user input. Parameterised queries / ORM only.
2. Every route is deny-by-default: explicit auth + role check + **object-level ownership check**
   (teachers only their own classes/students; later: every query scoped by `tenant_id`).
3. Every state-changing request is POST/PUT/PATCH/DELETE with CSRF protection. No state change on GET.
4. No secrets, keys, passwords, tokens or real student data in code, tests, logs, fixtures, or git history.
   Config via environment variables. Provide `.env.example` only.
5. No inline `<script>`, inline `style=`, `eval`, or `innerHTML` with untrusted data. Keep the strict CSP.
6. Validate all input server-side with allow-lists, length limits and types. Output is always escaped.
7. Never log passwords, tokens, session IDs or personal data of students/guardians.
8. Don't weaken, skip, or delete a security test to make a build pass. Fix the code.
9. New dependencies: justify in the PR, pin versions, check licence (no GPL/AGPL in the shipped product
   without asking me) and run `pip-audit`.
10. Errors shown to users never contain stack traces, SQL, or file paths.

## Engineering workflow
- Work in small, reviewable steps on a branch (`feat/…`, `fix/…`, `chore/…`). Conventional Commits.
- **Test first for behaviour you change**: write/extend tests, see them fail, then implement.
  Before refactoring legacy code, add characterization tests so behaviour is preserved.
- Before saying "done" you MUST run and report results of: `ruff check`, `ruff format --check`,
  `mypy` (once configured), `pytest --cov`, `bandit -r`, `pip-audit`. Paste failures honestly.
- Coverage targets: ≥85% overall, ≥95% on `security.py`, auth, permissions, attendance and fees logic.
- Public functions have type hints and short docstrings. Keep functions small; no copy-paste logic.
- Database changes ONLY via migrations (Alembic) that are reversible and tested on a copy of seed data.
- Don't do big-bang rewrites. Keep the app runnable and tests green after every commit.
- If requirements are ambiguous or a decision is architectural/irreversible, STOP and ask me with
  options + a recommendation. Record decisions as ADRs in `docs/adr/NNNN-title.md`.

## Product/UX rules
- Users are non-technical school staff: few clicks, plain language, clear error messages, mobile-friendly.
- Attendance entry for a class must stay possible in under 30 seconds.
- Terminology: Sabaq (new lesson), Sabqi (recent revision), Manzil (old revision), Nazra, Tajweed, Hifz.
- Accessibility: WCAG 2.1 AA basics (labels, contrast, keyboard use). Prepare for i18n (English, Urdu/RTL, Arabic).

## Definition of Done (every task)
Code + tests + docs updated, all checks green, no new security warnings, migration included if schema
changed, changelog entry added, and a short summary of what changed, what was tested, and remaining risks.
