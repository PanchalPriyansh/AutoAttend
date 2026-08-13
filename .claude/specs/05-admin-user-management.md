# Spec: Admin User Management

## Overview

AutoAttend has no public registration — `03-authentication` deliberately shipped only `flask create-admin`, a one-off CLI bootstrap, so the `users` collection currently has exactly one way to grow and it requires shell access to the server. `04-academic-hierarchy-management` built the full Institute → Department → Semester → Course → Class structure but stopped short of the "→ Student" tier: it left `classes.faculty_id` permanently `null` and treated `class_enrollments` as read-only, explicitly deferring both to this spec. This feature closes that gap. It gives the admin a REST API and an Admin Portal screen to provision faculty and student accounts, assign a faculty member to a class, and enroll students into a class. It is the last purely-CRUD spec on the roadmap and it unblocks everything after it: face enrollment (`06`) needs student accounts to attach encodings to, attendance capture (`07`) needs a class roster to mark present/absent against and an assigned faculty member authorized to take it, and the student dashboard (`09`), ML risk model (`10`), and notifications (`11`) all need a student identity to resolve.

## Depends on

- `01-project-foundation` — `create_app()` app factory, blueprint registration pattern, env-driven `Config`, React routing shell.
- `02-database-setup` — the `users` and `class_enrollments` collections, their `$jsonSchema` validators, the `uniq_email` / `idx_role` / `uniq_class_id_student_id` / `idx_student_id` indexes, and the collection-name constants in `backend/database/schema.py`.
- `03-authentication` — `role_required(*roles)` (`backend/auth/decorators.py`), `create_user` / `normalize_email` / `to_safe_profile` (`backend/auth/service.py`), `hash_password` (`backend/auth/passwords.py`), the HttpOnly-cookie JWT + CSRF setup, `apiFetch` (`frontend/src/api/client.js`), `AuthContext`, `ProtectedRoute`.
- `04-academic-hierarchy-management` — the `classes` collection as a real populated level, `Level` descriptors in `backend/academic/levels.py`, the validator/serializer/error patterns in `backend/academic/`, the cascading `AcademicHierarchy.jsx` screen, and `ConfirmDialog.jsx`.

## APIs

All endpoints live on a single new `users_bp` blueprint (`url_prefix="/api"`). **Every endpoint in this spec is `admin` only** — including reads. Faculty do not need a user directory to take attendance, and a class roster endpoint scoped to the requesting faculty member belongs to `07-attendance-capture`, which needs the ownership check anyway. Students get no access to any of it.

**User accounts**

- `GET /api/users?role=<role>&institute_id=<id>&q=<text>` — list users; all three filters optional, `role` restricted to `admin|faculty|student`, `q` matches name or email — **admin**
- `POST /api/users` — create `{ name, email, password, role, institute_id? }` — **admin**
- `GET /api/users/<id>` — fetch one user — **admin**
- `PUT /api/users/<id>` — update `{ name, email, institute_id }` — **admin**
- `PUT /api/users/<id>/status` — set `{ is_active: bool }` (deactivate / reactivate) — **admin**
- `PUT /api/users/<id>/password` — set `{ password }` (admin-initiated reset) — **admin**

**There is no `DELETE /api/users/<id>`.** Accounts are deactivated, never removed — a deleted user would orphan their enrollments, and later their attendance records and face encodings. Deactivation is already the mechanism `authenticate_user` and `POST /api/auth/refresh` honour.

**`role` is immutable after creation.** `PUT /api/users/<id>` ignores a `role` in the body. Promoting a student to faculty would silently invalidate their enrollments and grant a new privilege tier through an edit form; the supported path is to deactivate the account and create a new one.

**Faculty assignment**

- `PUT /api/classes/<class_id>/faculty` — set `{ faculty_id }`, or `{ faculty_id: null }` to unassign — **admin**

**Student enrollment**

- `GET /api/classes/<class_id>/students` — list students enrolled in the class — **admin**
- `POST /api/classes/<class_id>/students` — enroll `{ student_id }` — **admin**
- `DELETE /api/classes/<class_id>/students/<student_id>` — unenroll — **admin**

**Status codes** — identical contract to `01`–`04`, `{ "error": "..." }` on every failure:

- `200` — successful read/update/unenroll
- `201` — successful create/enroll, returning the created document
- `400` — missing or blank required field, malformed ObjectId, invalid `role`, password below the minimum length, `is_active` not a boolean, or an unknown `role` filter value
- `401` / `403` — unauthenticated / non-admin (handled by `role_required`)
- `404` — the user, class, referenced institute, or enrollment does not exist
- `409` — duplicate email, duplicate enrollment, or a self-protection rule violation (see Rules)
- `500` — database error, via blueprint-level `PyMongoError` / `RuntimeError` handlers mirroring `routes/auth.py` and `routes/academic.py`

**Serialized user shape** — `password_hash` must never appear in any response, in any endpoint, under any condition:

```json
{
  "id": "...",
  "name": "...",
  "email": "...",
  "role": "faculty",
  "institute_id": "...",
  "is_active": true,
  "created_at": "2026-08-13T00:00:00+00:00",
  "updated_at": "2026-08-13T00:00:00+00:00"
}
```

**Serialized enrollment shape** — the roster returns the enrolled student's profile plus the enrollment timestamp, so the UI needs one request rather than N:

```json
{ "id": "<enrollment id>", "student": { "id": "...", "name": "...", "email": "...", "is_active": true }, "enrolled_at": "2026-08-13T00:00:00+00:00" }
```

List endpoints return their array under a named key (`{ "users": [...] }`, `{ "enrollments": [...] }`), matching the `04` convention.

## Database changes

**No new collections, no validator changes, no index changes.** This feature is the first writer for `class_enrollments` and the first writer of a non-`null` `classes.faculty_id`. It must respect what `02-database-setup` already declared:

- `users` documents carry only the declared fields: `name`, `email`, `password_hash`, `role`, `institute_id`, `is_active`, `created_at`, `updated_at`. **Do not add roll numbers, phone numbers, department references, or any other undeclared field** — if a later feature genuinely needs one, it belongs in `schema.py` as its own change.
- `users.updated_at` must be refreshed on every mutation (profile edit, status change, password reset) and must be a real `datetime`, never an ISO string.
- `class_enrollments` documents carry exactly `class_id`, `student_id`, `enrolled_at`; `enrolled_at` is a BSON date.
- `users.email` is stored lowercased and trimmed via the existing `normalize_email`. Uniqueness comes from the `uniq_email` index — catch `DuplicateKeyError` and translate to `409` rather than pre-checking with a racy `find_one`.
- Duplicate enrollment relies on the existing `uniq_class_id_student_id` index, translated the same way.
- Deactivating a user **does not** delete their enrollments or clear their class assignment. The record stays so attendance history built on it in `07` remains interpretable.

## Frontend

- **Create:**
  - `frontend/src/api/users.js` — wrappers over `apiFetch` for the endpoints above, following the exact pattern of `frontend/src/api/academic.js`: unwrap JSON, throw an `Error` carrying the backend's `error` message on a non-OK response, so a `409` reaches the admin as the server's own wording. Do not re-implement cookie/CSRF/refresh handling — `apiFetch` owns it.
  - `frontend/src/routes/admin/UserManagement.jsx` — the user directory at `/admin/users`: filter by role and search text, create a user, edit name/email/institute, reset a password, and toggle active status. Shows deactivated accounts distinctly rather than hiding them.
  - `frontend/src/components/admin/UserForm.jsx` — one form used for both create and edit. In edit mode the `role` field is displayed read-only and the password field is absent, matching the backend rules.
  - `frontend/src/components/admin/ClassAssignment.jsx` — the panel for a single class: the currently assigned faculty member with a picker to change or clear it, and the enrolled-student roster with add/remove.

- **Modify:**
  - `frontend/src/App.jsx` — add `/admin/users` wrapped in `<ProtectedRoute role="admin">`.
  - `frontend/src/routes/AdminPortal.jsx` — add a link to the user directory alongside the existing academic-hierarchy link.
  - `frontend/src/routes/admin/AcademicHierarchy.jsx` — render `ClassAssignment` for `selection.class` when a class is selected. **Reuse the existing cascade rather than building a second institute→class selector**; the drill-down already exists and already clears descendant selections.
  - `frontend/src/index.css` — only if shared styling is genuinely needed. No CSS framework, no component library.
  - Reuse the existing `frontend/src/components/admin/ConfirmDialog.jsx` for deactivation and unenrollment confirmations. Do not write a second dialog.

## Backend

- **Create:**
  - `backend/common/__init__.py`
  - `backend/common/errors.py` — `ValidationError` (400), `NotFoundError` (404), `DuplicateError` (409), `ConflictError` (409). Both this feature and `04` need these; duplicating a parallel exception hierarchy under `users/` is exactly the unnecessary duplication `CLAUDE.md` forbids.
  - `backend/common/validators.py` — the generic input helpers moved verbatim from `academic/validators.py`: `parse_object_id`, `require_non_empty_string`, `parse_date`, `require_end_after_start`.
  - `backend/users/__init__.py`
  - `backend/users/validators.py` — user-specific input rules only: `require_role`, `require_password` (minimum length, defined as a module constant, not scattered literals), `require_bool`, `parse_optional_object_id` for the nullable `institute_id` / `faculty_id`.
  - `backend/users/serializers.py` — `serialize_user`, `serialize_users`, `serialize_enrollment`. Built from an explicit allow-list of fields so `password_hash` cannot leak by accident, mirroring how `academic/serializers.py` serializes from the level descriptor rather than from the raw document.
  - `backend/users/service.py` — all account business logic, **with no Flask request/response objects in any signature** (mirroring `auth/service.py` and `academic/service.py`): `list_users`, `get_user`, `create_managed_user`, `update_user`, `set_user_status`, `set_user_password`. `create_managed_user` delegates hashing, email normalization, and insertion to the existing `auth.service.create_user` instead of re-implementing them, translating `DuplicateEmailError` into `DuplicateError`.
  - `backend/users/assignments.py` — the two cross-collection operations: `assign_faculty`, `list_enrollments`, `enroll_student`, `unenroll_student`. Kept out of `service.py` because these touch `classes` and `class_enrollments`, not just `users`.
  - `backend/routes/users.py` — the `users_bp` blueprint with all ten endpoints. Handlers stay thin: `role_required("admin")`, parse and validate input, call the service, map domain exceptions to status codes via blueprint-level error handlers, serialize, return.

- **Modify:**
  - `backend/academic/errors.py` — re-export `ValidationError`, `NotFoundError`, `DuplicateError` from `common.errors`; keep `HasChildrenError` here as a subclass of `ConflictError`. **Every existing import path must keep working unchanged** — `academic/service.py`, `academic/validators.py`, and `routes/academic.py` continue to import from `academic.errors` and must resolve to the same class objects, so the existing `errorhandler` registrations still match.
  - `backend/academic/validators.py` — re-export the four helpers from `common.validators`. `routes/academic.py` keeps importing from `academic.validators`. No behaviour change, no signature change, no message change.
  - `backend/app.py` — register `users_bp`; apply the same password-length rule to `create-admin` so the CLI bootstrap and the API cannot disagree about what a valid password is. No other change.
  - `CLAUDE.md` — update the "Implemented vs stub features" table: mark Admin user management implemented, note the now-satisfied deferrals from the Academic hierarchy row (faculty assignment, student enrollment), and state what remains deferred.

## Files to change

- `backend/academic/errors.py`
- `backend/academic/validators.py`
- `backend/app.py`
- `CLAUDE.md`
- `frontend/src/App.jsx`
- `frontend/src/routes/AdminPortal.jsx`
- `frontend/src/routes/admin/AcademicHierarchy.jsx`
- `frontend/src/index.css` (only if shared styles are required)

## Files to create

- `backend/common/__init__.py`
- `backend/common/errors.py`
- `backend/common/validators.py`
- `backend/users/__init__.py`
- `backend/users/validators.py`
- `backend/users/serializers.py`
- `backend/users/service.py`
- `backend/users/assignments.py`
- `backend/routes/users.py`
- `frontend/src/api/users.js`
- `frontend/src/routes/admin/UserManagement.jsx`
- `frontend/src/components/admin/UserForm.jsx`
- `frontend/src/components/admin/ClassAssignment.jsx`

## New dependencies

**No new dependencies.** `Flask`, `Flask-JWT-Extended`, `pymongo`, and `werkzeug.security` cover the backend; `react-router-dom` and the existing `apiFetch` cover the frontend. Do not add `marshmallow`/`pydantic`, an ODM, a permissions library, a UI kit, or a data-fetching library. **Do not add `opencv`, `face_recognition`, `numpy`, `scikit-learn`, or any SMTP/email package** — those belong to `06`, `10`, and `11`, and installing them here would be the mid-feature dependency creep `CLAUDE.md` prohibits.

## Rules for implementation

- **Every endpoint is guarded on the backend** with `role_required("admin")` from `auth/decorators.py`. `ProtectedRoute` and hidden UI are conveniences only — an authenticated faculty or student calling any of these endpoints directly must receive `403`.
- **`password_hash` never leaves the backend.** Serialize from an explicit field allow-list, never `dict(user)` minus a key. Reject any pull request shape where a raw user document reaches `jsonify`.
- **Passwords are never logged, echoed back, or returned** — not in a create response, not in an error message, not in a server log line. Hashing goes through `auth/passwords.py` only; no endpoint ever accepts a pre-hashed value.
- **Self-protection rules, enforced in the service, not the UI:**
  - An admin cannot deactivate their own account (`409`).
  - An admin cannot deactivate the last remaining active admin (`409`) — the system must never be left with no way in.
  - `role` cannot be changed after creation.
  - The acting admin's id comes from the JWT identity, never from the request body.
- **Referenced entities are verified before they are stored.** `institute_id` must resolve in `institutes`; `faculty_id` must resolve to a user with `role: "faculty"` and `is_active: true`; `student_id` must resolve to a user with `role: "student"` and `is_active: true`; `class_id` must resolve in `classes`. A wrong-role id is a `400`, a non-existent id is a `404` — never a silently stored dangling reference.
- **Import collection names from `database/schema.py`** (`USERS`, `CLASSES`, `INSTITUTES`, `CLASS_ENROLLMENTS`). No hardcoded `"users"` string literals anywhere.
- **Keep business logic out of route handlers**, per `CLAUDE.md`. `routes/users.py` translates HTTP ↔ domain; `users/service.py` and `users/assignments.py` own the rules and hold no Flask objects.
- **Reuse before rewriting.** `auth.service.create_user`, `auth.service.normalize_email`, `auth.passwords.hash_password`, `common/validators.py`, and `ConfirmDialog.jsx` already exist. A second email-normalization function or a second ObjectId parser is a defect.
- **The `academic/` refactor is a move, not a redesign.** Extracting the shared helpers into `common/` must not change a single validation rule, error message, status code, or function signature. If any `04` test needs editing to pass, the refactor went too far — revert and re-do it as a pure move.
- **One faculty member per class** — `classes.faculty_id` is a single field, and this spec does not change the schema to support co-teaching. Assigning replaces any previous assignment. A faculty member may hold multiple classes.
- **Deactivation is not deletion and does not cascade.** A deactivated faculty member stays assigned to their classes and a deactivated student stays enrolled; the UI surfaces the inactive state so the admin can act deliberately. Nothing in this feature deletes an enrollment implicitly.
- **Class deletion stays blocked by enrollments.** The `04` rule (`409` when `class_enrollments` reference a class) now becomes reachable in practice — it must keep working, not be relaxed to make enrollment cleanup convenient.
- **Never hardcode academic data or user data.** No seed faculty, no sample students, no default institute in application code, config, or migrations.
- **Validate ObjectIds before querying.** A malformed id returns `400`, never a raw `bson.errors.InvalidId` traceback or a `500`.
- **Never leak internals.** `DuplicateKeyError` details, `$jsonSchema` validator failures, and driver exceptions are translated into the clean JSON contract; raw MongoDB text does not reach the client. Log server-side instead.
- **Account enumeration:** these endpoints are admin-only, so a precise "email already exists" `409` is acceptable here. This must not change the deliberately indistinguishable failure behaviour of `POST /api/auth/login`, which stays exactly as `03` built it.
- **Known limitation to document, not to solve here:** an admin-initiated password reset does not invalidate that user's already-issued access token, which remains valid for up to its 15-minute `JWT_ACCESS_TOKEN_EXPIRES` lifetime. Deactivation is already bounded the same way, since `POST /api/auth/refresh` re-checks `is_active`. **Do not build a token blocklist, a token-version claim, or a session store in this spec** — note the limitation in a code comment and leave it for a dedicated change.
- **Preserve existing functionality.** `/api/health`, `flask init-db`, `flask create-admin`, all auth endpoints, all twenty academic endpoints, `/admin/academics`, and the `01`–`04` test suites must continue to work. The app must still start when MongoDB is unreachable.
- **Do not touch face-recognition, attendance, ML, or notification code**, and do not create their collections.
- **Tests must not use real production credentials, a production database, real personal data, or any biometric data.**

## Definition of done

- [ ] `python app.py` starts with `users_bp` registered; `/api/health` still responds `200` and all twenty academic endpoints still respond as before.
- [ ] As an admin, a faculty account and a student account can each be created via `POST /api/users` with `201` and the created user returned.
- [ ] **No response from any endpoint contains `password_hash`** — verified for create, get, list, update, status, password reset, and the enrollment roster.
- [ ] The created faculty user can log in through `POST /api/auth/login` with the password the admin set and lands on the faculty portal; the created student likewise.
- [ ] Creating a user with an email that already exists returns `409`; the comparison is case-insensitive (`A@b.com` collides with `a@b.com`) and the stored email is lowercased and trimmed.
- [ ] Creating a user with a password below the minimum length returns `400`, and `flask create-admin` rejects the same password.
- [ ] Creating a user with an invalid `role`, a malformed `institute_id`, or a non-existent `institute_id` returns `400`/`400`/`404` respectively — never a `500` or a traceback.
- [ ] `GET /api/users?role=faculty` returns only faculty; `?role=bogus` returns `400`; `?institute_id=` and `?q=` narrow the list correctly and combine.
- [ ] `PUT /api/users/<id>` updates name, email, and institute, bumps `updated_at`, and **ignores a `role` sent in the body** — the stored role is unchanged.
- [ ] `PUT /api/users/<id>/status` with `{ "is_active": false }` deactivates the account, after which login fails and `POST /api/auth/refresh` with that user's existing refresh cookie returns `401`; reactivating restores login.
- [ ] An admin attempting to deactivate **their own** account returns `409` and the account stays active.
- [ ] Deactivating the **last active admin** returns `409`; with a second active admin present, the same call succeeds.
- [ ] `PUT /api/users/<id>/password` changes the password: the old one fails at login and the new one succeeds.
- [ ] `PUT /api/classes/<id>/faculty` assigns a faculty user, and `GET /api/classes?course_id=<id>` then returns that class with the matching `faculty_id` as a string.
- [ ] Assigning a **student** or **admin** id as `faculty_id` returns `400`; assigning a non-existent id returns `404`; `{ "faculty_id": null }` clears the assignment back to `null`.
- [ ] `POST /api/classes/<id>/students` enrolls a student with `201`, writes `enrolled_at` as a BSON date (verified in MongoDB, `typeof` is `date`, not `string`), and enrolling the same student twice returns `409`.
- [ ] Enrolling a **faculty** or **admin** id returns `400`; enrolling into a non-existent class returns `404`.
- [ ] `GET /api/classes/<id>/students` returns only that class's roster with each student's name, email, and active flag — verified against two populated classes, with no cross-class leakage.
- [ ] `DELETE /api/classes/<id>/students/<student_id>` removes the enrollment with `200`; repeating it returns `404`.
- [ ] Deleting a class that still has enrollments returns `409` (the `04` rule, now reachable with real enrollment data), and succeeds once the students are unenrolled.
- [ ] `DELETE /api/users/<id>` does not exist — the route returns `404`/`405`, and no code path hard-deletes a user document.
- [ ] Every response exposes ids as strings and dates as ISO strings; no raw `ObjectId(...)` or BSON artifacts appear in JSON.
- [ ] An authenticated **faculty** token receives `403` on all ten endpoints, an authenticated **student** token receives `403`, and an unauthenticated request receives `401`.
- [ ] A `POST`/`PUT`/`DELETE` with valid cookies but a missing `X-CSRF-TOKEN` header is rejected, confirming CSRF protection applies to the new routes.
- [ ] `/admin/users` lists users with role filtering and search, creates a user, edits one, resets a password, and toggles active status — each updating the list without a full page reload, with confirmation before deactivation.
- [ ] In `/admin/academics`, selecting a class reveals the assignment panel; assigning faculty and enrolling/removing students works there, and changing the selected course clears it.
- [ ] A backend `400`/`404`/`409` surfaces in the UI as the server's own message, not a generic failure.
- [ ] The `academic/` refactor changed no behaviour: **the `04` test suite passes completely unmodified**, and `01`–`03` pass too.
- [ ] `CLAUDE.md`'s "Implemented vs stub features" table reflects the new Admin user management status and the resolved `04` deferrals.
