# Spec: Database Setup

## Overview

This feature establishes the MongoDB collection structure for AutoAttend's core domain model: the academic hierarchy (`Institute → Department → Semester → Course → Class`) plus `users` (admin/faculty/student accounts) and `class_enrollments` (the student-to-class relationship). It defines each collection with a JSON Schema validator, sets up the required indexes (uniqueness constraints and lookup indexes), and provides an idempotent, explicitly-invoked initialization routine. No authentication, CRUD APIs, attendance, face recognition, or ML logic is implemented here — this is purely the database layer that the Authentication and Academic Hierarchy Management features will build on top of next.

## Depends on

`01-project-foundation` — requires the existing Flask app factory, `Config`, and `backend/database/db.py` MongoDB connection helper.

## APIs

No API changes. This feature only defines the database layer; CRUD endpoints for academic hierarchy and authentication belong to later specs.

## Database changes

New collections, each created with a MongoDB `$jsonSchema` validator (`validationLevel: "strict"`) and indexes:

- **`users`** — `name`, `email` (unique, lowercase), `password_hash`, `role` (enum: `admin` | `faculty` | `student`), `institute_id` (ObjectId ref, nullable for a not-yet-scoped admin), `is_active` (bool, default `true`), `created_at`, `updated_at`.
  - Unique index on `email`.
  - Index on `role`.
- **`institutes`** — `name`, `code` (unique), `created_at`.
  - Unique index on `code`.
- **`departments`** — `institute_id` (ObjectId ref → `institutes`), `name`, `code`, `created_at`.
  - Unique compound index on `(institute_id, code)`.
- **`semesters`** — `department_id` (ObjectId ref → `departments`), `name` (e.g. `"Sem 1"`), `start_date`, `end_date`, `created_at`.
  - Unique compound index on `(department_id, name)`.
- **`courses`** — `semester_id` (ObjectId ref → `semesters`), `name`, `code`, `created_at`.
  - Unique compound index on `(semester_id, code)`.
- **`classes`** — `course_id` (ObjectId ref → `courses`), `name` (e.g. section label), `faculty_id` (ObjectId ref → `users`, nullable until assigned), `created_at`.
  - Unique compound index on `(course_id, name)`.
  - Index on `faculty_id`.
- **`class_enrollments`** — `class_id` (ObjectId ref → `classes`), `student_id` (ObjectId ref → `users`), `enrolled_at`. Kept as its own collection (not an embedded array on `classes` or `users`) to avoid unbounded-array growth per class/student.
  - Unique compound index on `(class_id, student_id)`.
  - Index on `student_id`.

References between levels are stored as ObjectId fields (not embedded documents), matching the strict top-down hierarchy in `CLAUDE.md`. Attendance records, face-encoding data, and ML feature data are explicitly out of scope for this spec and will be introduced by their own feature specs.

## Frontend

No frontend changes.

## Backend

- **Create:**
  - `backend/database/schema.py` — declares, per collection, its name, `$jsonSchema` validator dict, and list of index specs (fields + unique flag) as plain data structures.
  - `backend/database/init_db.py` — `init_database(db)` function that idempotently creates each collection (via `create_collection` with the validator if missing, or `collMod` to update the validator if the collection already exists) and calls `create_index` for every index spec (safe to re-run).
- **Modify:**
  - `backend/database/db.py` — add a `get_db()` helper that returns the `pymongo` `Database` object (parsed from `MONGODB_URI`) for reuse by `init_db.py` and future feature modules; keep the existing `get_db_status()` behavior unchanged.
  - `backend/app.py` — register an explicit `flask init-db` CLI command (via `app.cli.command`) that calls `init_database(get_db())`. Do **not** call this automatically on normal app startup — the app must keep starting and serving `/api/health` even when MongoDB is unreachable, per the existing project-foundation guarantee.

## Files to change

- `backend/database/db.py`
- `backend/app.py`

## Files to create

- `backend/database/schema.py`
- `backend/database/init_db.py`

## New dependencies

No new dependencies — `pymongo` (already installed) natively supports `$jsonSchema` validators and index creation.

## Rules for implementation

- Collections and indexes are defined as data in `schema.py`, not scattered inline — later features (auth, academic hierarchy CRUD) must import collection names/constants from here rather than hardcoding string literals.
- `init_database()` must be idempotent: safe to run repeatedly against the same database without errors or duplicate indexes.
- `init_database()` must never run implicitly on normal `python app.py` / `flask run` startup — it is only triggered via the explicit `flask init-db` CLI command, preserving the guarantee that the API starts even when MongoDB is down.
- Use ObjectId references between hierarchy levels, never embedding one level's full documents inside another, consistent with the top-down, database-driven academic hierarchy described in `CLAUDE.md`.
- Do not seed real or fake production-looking data (institutes, departments, users, etc.) as part of this feature — seeding/creation of actual records belongs to the Admin Portal / Authentication features.
- Do not add password-hashing, JWT, or any auth logic here — the `users` collection schema only defines the field shape; login/session logic is a separate spec.
- Do not create collections for attendance, face encodings, or ML features — out of scope until their respective specs.
- Keep `schema.py` and `init_db.py` free of Flask route/request handling — this is pure database-layer code.
- No hardcoded credentials; continue reading `MONGODB_URI` from `Config`/environment variables only.

## Definition of done

- [ ] `backend/database/schema.py` defines validators and index specs for `users`, `institutes`, `departments`, `semesters`, `courses`, `classes`, and `class_enrollments`.
- [ ] Running `flask init-db` (from `backend/`, with a reachable MongoDB) creates all seven collections with their validators applied and all listed indexes present.
- [ ] Running `flask init-db` a second time against the same database completes without errors and does not create duplicate indexes or collections.
- [ ] Inserting a document that violates a collection's schema (e.g. `role` outside the allowed enum, missing required field) is rejected by MongoDB with a validation error.
- [ ] Inserting a duplicate value on a uniquely-indexed field (e.g. a second `users` document with the same `email`, or a second `institutes` document with the same `code`) is rejected with a duplicate-key error.
- [ ] `GET /api/health` and normal app startup (`python app.py`) behavior are unchanged — the app still starts and reports a degraded (not crashed) status when MongoDB is unreachable, and `flask init-db` is not invoked automatically.
- [ ] No attendance, face-recognition, or ML-related collections are created by this feature.
- [ ] No secrets or real academic/user data are committed anywhere in this feature's code.
