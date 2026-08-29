# Spec: Init-DB Index Reconciliation

## Overview

`flask init-db` is the first command the README tells a newcomer to run, and against a database whose indexes have drifted from `database/schema.py` it aborts part-way through with `IndexKeySpecsConflict` — leaving some collections set up and others not. This is not hypothetical: during the `10-low-attendance-notifications` live run it failed against the project's own Atlas database, because `attendance_sessions` carried `uniq_class_id_date` keyed `{class_id: 1, date: -1}` while `schema.py` has declared `{class_id: 1, date: 1}` since `02-database-setup`. `git log -S` confirms `-1` was never in `schema.py`, so that index was created outside the schema by something else. MongoDB will not change an index's key shape in place under the same name — it raises instead — and because the failure happens mid-pass, the newly-added `attendance_notifications` index was never created at all.

`init_db.py` already knows about this. Its own comment at the `create_index` call says that a changed key shape "raises IndexOptionsConflict rather than updating it in place — a manual drop_index would be needed", and then does nothing about it. This feature closes that gap: `init_database` learns to compare each declared index against what the database actually has, and to rebuild the ones that no longer match, so the command's documented promise — safe to re-run, makes the database match `schema.py` — holds against a real database and not only against an empty one.

It is deliberately narrow. **No new collection, no new field, no new index declaration, no new endpoint, no new page, no new dependency.** Nothing about the schema changes; only the code that applies it does. The one behavioural addition beyond the fix itself is a `--dry-run` flag, because this is the first thing in the project that *drops* something from a live database and the operator should be able to see the plan first — the same courtesy `flask notify-low-attendance --dry-run` already offers before it sends mail.

## Depends on

- `02-database-setup` — everything this touches: `database/schema.py`'s `COLLECTIONS` (the declared index specs, unchanged by this feature), `database/init_db.py`'s `init_database(db)`, `database/db.py`'s `get_db()`, and the `flask init-db` command registered in `app.py`.
- `06-face-enrollment`, `07-attendance-capture`, `10-low-attendance-notifications` — each added collections and indexes to `COLLECTIONS`; `10` is where the drift was found, and its `attendance_notifications` index is the one the aborted run never reached.

## APIs

**No API changes.** This feature adds no route, no blueprint, and no request path of any kind. `init-db` is a CLI command and stays one — the same discipline `10` applied to notifications. Nothing here is reachable over HTTP.

## Database changes

**No schema changes.** `database/schema.py` is not edited: no collection, validator, field, or index declaration is added, removed, or altered. `COLLECTIONS` remains the single declaration of intent, and this feature only changes how faithfully that declaration is applied to a database that has drifted from it.

What changes is the *effect* of running `flask init-db` against a drifted database: an index whose live definition no longer matches its declaration is dropped and recreated to match. This rebuilds an index; it reads and writes no documents, and it deletes no data.

Two drift shapes must be handled, because MongoDB refuses `create_index` for both:

1. **Same name, different definition** — the live case. `uniq_class_id_date` exists with keys `{class_id: 1, date: -1}`; the declaration says `{class_id: 1, date: 1}`. The same conflict arises if `unique` differs, or if the live index carries an option the declaration does not (`partialFilterExpression`, `collation`, `expireAfterSeconds`, `sparse`).
2. **Same key pattern, different name** — an index keyed exactly as declared but named something else. MongoDB rejects a second index with the same key pattern under a new name, so the declared name can never be created while it stands.

In both cases the declaration wins: the conflicting index is dropped and the declared one is created.

## Frontend

**No frontend changes**, and this is not an oversight. The 2026-08-28 rule that a feature ships its designed, responsive UI in the same cycle applies to features that have a user-facing surface; this one is a CLI command run from a terminal by whoever administers the deployment. Adding a screen for it would mean putting a "drop an index on the production database" button in a web app, which is the opposite of what this feature is for.

## Backend

- **Create:**
  - `backend/database/indexes.py` — the comparison and planning logic, kept out of `init_db.py` so it can be tested as what it is: a pure function over data.
    - A normalisation helper turning one entry from `Collection.index_information()` into the same comparable shape as a declared spec (keys as an ordered list of `(field, direction)` pairs, `unique` coerced from an absent key to `False`, and any option outside a known-inert allowlist — `v`, `key`, `unique`, `name`, `ns` — retained so its presence registers as a difference).
    - `plan_indexes(declared_specs, existing_info)` — pure, no pymongo import, no I/O. Returns one entry per declared index with an action of `create` (nothing there yet), `keep` (already matches), or `recreate` (a conflicting index must be dropped first), where a `recreate` entry names the index to drop, the reason it conflicts, and whether the index being created is unique. Never returns an action for an index the project did not declare.
  - **Rationale for a separate module:** `init_db.py` is 44 lines of orchestration and should stay that. The interesting part of this feature is a decision about two dictionaries, which deserves direct tests that construct `index_information()` output by hand rather than driving it through a fake database. `schema.py` is already the precedent for a pure-data module in this package.
  - `backend/database/reporting.py` — `format_index_report(result, dry_run=False) -> (lines, error_lines)`, mirroring `notifications/reporting.py` exactly, including returning lines rather than printing so the stdout/stderr choice stays with the command. That module's own docstring records that `init-db` "translates a call into a single `click.echo`" and that presentation must not migrate into `app.py`; this feature is what breaks that premise, since the output goes from one line to a line per changed index. Keeping the formatter in `indexes.py` would mix planning with presentation, which is the same split `notifications/` already made between `messages.py` and `reporting.py`.
- **Modify:**
  - `backend/database/init_db.py` — the index pass calls `plan_indexes` against `collection.index_information()` instead of calling `create_index` blindly, then applies the plan: for a `recreate`, run the duplicate pre-check (below) if the declared index is unique, `drop_index` the conflicting name, then `create_index`. Accepts a `dry_run` argument (default `False`) that computes and returns the plan while performing no `drop_index`, `create_index`, `create_collection`, or `collMod` call. The existing return value gains an index report; `{"collections": [...]}` keeps its current key and meaning so nothing that reads it breaks.
  - `backend/app.py` — `init-db` gains a `--dry-run` flag and reports what happened: the collections line it already prints, plus a line per index that was created or recreated (naming the reason for a recreate) and a count of those left unchanged. A run blocked by the duplicate pre-check prints which collection and which duplicate key values blocked it and exits non-zero. Everything else about the command — the broad `except` that logs the traceback and prints a message without leaking the exception object, `SystemExit(1)`, not running at startup — stays exactly as it is.

## Files to change

- `backend/database/init_db.py`
- `backend/app.py`
- `backend/tests/test_init_db.py` — `FakeCollection` gains `index_information()` and `drop_index()`; existing assertions about `create_index` calls are updated to account for a fake that now reports what it already has.
- `backend/tests/test_cli.py` — the `init-db` command's new output, `--dry-run`, and the non-zero exit on a blocked run.

## Files to create

- `backend/database/indexes.py`
- `backend/database/reporting.py`
- `backend/tests/test_indexes.py`
- `backend/tests/test_database_reporting.py`

## New dependencies

**No new dependencies.** `pymongo` (already installed) provides `index_information()` and `drop_index()`, and the comparison is plain Python.

## Rules for implementation

- **Never drop an index the project did not declare.** Only an index that collides with an entry in `COLLECTIONS` — by name, or by key pattern — may be dropped. `_id_` is never declared and must never be touched, and neither may an operator's ad-hoc index that conflicts with nothing. A tidy-up pass that removes undeclared indexes is explicitly out of scope: this feature makes the declared set correct, it does not claim ownership of the whole index space.
- **Check for duplicates before building any unique index, and abort that index rather than drop it.** The one genuinely dangerous sequence is: drop a unique index, fail to recreate it because the collection now holds documents the new key shape forbids, and leave the collection unconstrained. Pre-check with an aggregation grouping on the declared key fields and matching `count > 1`; if any group comes back, do not drop, report the collection, the index, and the offending key values, and exit non-zero. Fix the data first — the same order that resolved the live incident by hand.
  - The check applies to a **first creation** of a unique index as well as to a rebuild, not only to the drop case this rule was first written for. The failure mode is identical — `create_index(unique=True)` over data that already violates it raises and aborts the run mid-pass, which is the exact bug this spec exists to remove — and it costs one aggregation to turn a crash into a reported block. It is skipped only for a collection this run just created, which is empty by construction.
  - The check is a **read**, so `--dry-run` performs it too. A preview that cannot tell the operator the real run will be blocked is not much of a preview.
- **Order within one index is: check, drop, create.** Never drop every conflicting index in a first pass and create in a second — a failure between the passes would leave several collections unconstrained instead of one.
- **A `recreate` must state its reason**, and the reason must reach the operator's terminal, not just a log line. "Recreated `attendance_sessions.uniq_class_id_date`: key spec was `{class_id: 1, date: -1}`, declared `{class_id: 1, date: 1}`" is the point of the feature; "Initialized collections: …" alone hides exactly the event worth seeing.
- **`--dry-run` must touch nothing.** No `create_collection`, no `collMod`, no `create_index`, no `drop_index` — reads only. It must work against a drifted database and print the same plan the real run would execute.
- **Idempotency is the contract and must survive.** A second run immediately after a successful one reports every index as unchanged and issues no drop and no create. The existing tests that assert re-running does not error stay true.
- **Validator drift is out of scope.** `collMod` already reapplies a validator in place without conflict, which is why the collection pass never failed. Do not add validator comparison, reporting, or repair here.
- **Do not change `database/schema.py`.** If a declared index is found to be wrong, that is a separate spec. The drifted index in the live database is wrong against a declaration that has been correct since `02`.
- Keep the database layer separate from application logic, per `CLAUDE.md`: nothing in `indexes.py` or `init_db.py` may import from `attendance/`, `users/`, `recognition/`, or `notifications/`.
- No secrets, no credentials, and no document contents in any printed or logged output. The duplicate report names key *values* for the declared key fields only (class ids and dates, for the live case) — never a whole document, and never anything from `users`, `face_encodings`, or `attendance_notifications` beyond the keys of the index being rebuilt.
- Preserve existing behaviour: `create_app()` still never touches MongoDB, `/api/health` still works with no database, and `init-db` remains an explicit command that is never auto-run.

## Definition of done

- [ ] `backend/database/indexes.py` exists and imports neither `pymongo` nor Flask.
- [ ] `plan_indexes` returns `create` for a declared index absent from `index_information()`.
- [ ] `plan_indexes` returns `keep` for a declared index whose live definition matches exactly, including key order and direction.
- [ ] `plan_indexes` returns `recreate` for each of: a differing key direction (the live `{class_id: 1, date: -1}` case), a differing key order, a differing field set, a differing `unique`, and a live index carrying an extra option (`partialFilterExpression`, `collation`, `expireAfterSeconds`, or `sparse`) the declaration does not.
- [ ] `plan_indexes` returns `recreate` naming the *existing* index for a live index whose key pattern matches a declaration but whose name does not.
- [ ] `plan_indexes` returns no action for `_id_` or for any other undeclared index, and never names one as the index to drop.
- [ ] Each `recreate` entry carries a human-readable reason naming the live definition and the declared one.
- [ ] `init_database` calls `index_information()` per collection and issues `drop_index` only for names a plan marked for recreation.
- [ ] Against a fake database seeded with `attendance_sessions.uniq_class_id_date` keyed `{class_id: 1, date: -1}`, `init_database` drops that index and creates it with `{class_id: 1, date: 1}`, and — the regression that motivated this spec — still creates every index on every *later* collection in `COLLECTIONS`, including `attendance_notifications.idx_student_id_class_id_sent_at`.
- [ ] When a unique index is to be recreated and the collection holds duplicate values for its declared key fields, `init_database` performs no `drop_index` for it, and the failure names the collection, the index, and the duplicate key values.
- [ ] A duplicate found in one collection does not silently skip the rest: the command exits non-zero and says what was and was not applied.
- [ ] `init_database(db, dry_run=True)` issues no `create_collection`, `command`, `create_index`, or `drop_index` call at all, and returns the same plan a real run would apply.
- [ ] `flask init-db --dry-run` prints that plan and exits zero; `flask init-db` applies it.
- [ ] `flask init-db` output names every index created or recreated, with the reason for each recreate, plus a count of unchanged indexes.
- [ ] Running `init_database` twice against the same fake database reports every index unchanged on the second run and issues zero `drop_index` and zero `create_index` calls.
- [ ] The collection pass still precedes the index pass, and an existing collection still receives `collMod` rather than `create_collection` (the existing ordering and idempotency tests in `test_init_db.py` still pass).
- [ ] `create_app()` still triggers no MongoDB access, and `/api/health` still responds with MongoDB unreachable (`test_cli.py`'s existing startup tests still pass).
- [ ] The failure path still logs the traceback and prints a message that does not leak the raw exception object, and still exits non-zero.
- [ ] `database/schema.py` is byte-identical to `main`.
- [ ] The full backend suite passes (`pytest`), with new tests in `test_indexes.py`, `test_init_db.py`, and `test_cli.py` covering the above.
- [ ] `flask init-db` runs cleanly against the real drifted Atlas database, reconciling `attendance_sessions.uniq_class_id_date` and creating the `attendance_notifications` index the aborted run never reached; a second run immediately after reports no changes.
