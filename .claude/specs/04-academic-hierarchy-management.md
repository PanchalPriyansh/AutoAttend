# Spec: Academic Hierarchy Management

## Overview

This feature makes AutoAttend's academic structure editable. `02-database-setup` created the `institutes`, `departments`, `semesters`, `courses`, and `classes` collections with their validators and scoped unique indexes, but nothing in the application can read or write them yet — the hierarchy exists only as empty collections. This spec adds admin-only CRUD REST endpoints for all five levels, a service layer that enforces parent-child integrity (a child cannot exist without a valid parent, and a parent cannot be deleted while children reference it), and an Admin Portal screen that lets an admin browse and edit the hierarchy as cascading levels. Every later feature depends on this data being real and database-driven: faculty select an institute/department/semester/course/class before taking attendance, student dashboards resolve their class context, and the ML/notification features aggregate along these boundaries. `CLAUDE.md` forbids hard-coding any of these entities, so this feature is the only sanctioned way they enter the system.

## Depends on

- `01-project-foundation` — Flask app factory (`create_app`), env-driven `Config`, blueprint registration pattern, React routing shell.
- `02-database-setup` — the five hierarchy collections, their `$jsonSchema` validators, the collection-name constants in `backend/database/schema.py`, the scoped unique indexes (`uniq_code`, `uniq_institute_id_code`, `uniq_department_id_name`, `uniq_semester_id_code`, `uniq_course_id_name`), and `get_db()`.
- `03-authentication` — `role_required(*roles)` from `backend/auth/decorators.py`, the HttpOnly-cookie JWT setup, `apiFetch` in `frontend/src/api/client.js`, `AuthContext`, and `ProtectedRoute`.

## APIs

All endpoints are registered on a single `academic_bp` blueprint. **Every write (`POST`/`PUT`/`DELETE`) is `admin` only. Reads are `admin` and `faculty`** — faculty need the hierarchy to select an attendance context; student-scoped data belongs to the Student Dashboard spec, so students are not granted access to these list endpoints here.

**Institutes**

- `GET /api/institutes` — list all institutes — **admin, faculty**
- `POST /api/institutes` — create `{ name, code }` — **admin**
- `PUT /api/institutes/<id>` — update `{ name, code }` — **admin**
- `DELETE /api/institutes/<id>` — delete, blocked if departments reference it — **admin**

**Departments**

- `GET /api/departments?institute_id=<id>` — list departments of one institute; `institute_id` is **required** — **admin, faculty**
- `POST /api/departments` — create `{ institute_id, name, code }` — **admin**
- `PUT /api/departments/<id>` — update `{ name, code }` — **admin**
- `DELETE /api/departments/<id>` — delete, blocked if semesters reference it — **admin**

**Semesters**

- `GET /api/semesters?department_id=<id>` — list semesters of one department; `department_id` is **required** — **admin, faculty**
- `POST /api/semesters` — create `{ department_id, name, start_date, end_date }` — **admin**
- `PUT /api/semesters/<id>` — update `{ name, start_date, end_date }` — **admin**
- `DELETE /api/semesters/<id>` — delete, blocked if courses reference it — **admin**

**Courses**

- `GET /api/courses?semester_id=<id>` — list courses of one semester; `semester_id` is **required** — **admin, faculty**
- `POST /api/courses` — create `{ semester_id, name, code }` — **admin**
- `PUT /api/courses/<id>` — update `{ name, code }` — **admin**
- `DELETE /api/courses/<id>` — delete, blocked if classes reference it — **admin**

**Classes**

- `GET /api/classes?course_id=<id>` — list classes of one course; `course_id` is **required** — **admin, faculty**
- `POST /api/classes` — create `{ course_id, name }` — **admin**
- `PUT /api/classes/<id>` — update `{ name }` — **admin**
- `DELETE /api/classes/<id>` — delete, blocked if `class_enrollments` reference it — **admin**

**Parent references are immutable.** `PUT` never moves a document to a different parent (no re-parenting a department to another institute); the parent id field is ignored if sent. Moving a subtree is out of scope — delete and recreate.

**Status codes** (consistent with the `01`–`03` JSON error contract, `{ "error": "..." }` on failure):

- `200` — successful read/update/delete
- `201` — successful create, returning the created document
- `400` — missing/blank required field, malformed ObjectId, malformed date, `end_date` not after `start_date`, or a missing required parent query param
- `401` / `403` — unauthenticated / wrong role (handled by `role_required`)
- `404` — the target document, or the referenced parent, does not exist
- `409` — duplicate within the parent scope (violates a unique index), or delete blocked by existing children
- `500` — database error, via a blueprint-level handler mirroring `routes/auth.py`

**Serialized response shape** — every hierarchy document is returned with `_id` and all ObjectId reference fields converted to strings, and dates as ISO 8601 strings:

```json
{ "id": "...", "institute_id": "...", "name": "...", "code": "...", "created_at": "2026-08-12T00:00:00+00:00" }
```

List endpoints return a JSON array under a named key (e.g. `{ "departments": [...] }`) rather than a bare array, so the shape can grow later without breaking clients.

## Database changes

**No new collections, no validator changes, no index changes.** This feature is the first writer for the five existing hierarchy collections and must respect what `02-database-setup` already declared:

- Insert only the fields declared in each validator. `created_at` is required on every level and must be a real Python `datetime` (BSON date), never an ISO string — `schema.py` calls this out explicitly.
- `semesters.start_date` / `end_date` are BSON dates; parse incoming ISO date strings into `datetime` objects before insert.
- `classes.faculty_id` stays `null` on create — faculty assignment is **out of scope** (see Rules).
- **Do not add `updated_at`, `is_active`, or any other undeclared field** to hierarchy documents. If a later feature genuinely needs one, it belongs in `schema.py` as its own change, not smuggled in here.
- Duplicate handling relies on the existing unique indexes; catch `DuplicateKeyError` and translate to `409` rather than pre-checking with a racy `find_one`.

## Frontend

- **Create:**
  - `frontend/src/api/academic.js` — typed-by-convention wrappers over `apiFetch` for the endpoints above (`listInstitutes`, `createDepartment`, `deleteCourse`, …). Each parses the JSON body and throws an `Error` carrying the server's `error` message on a non-OK response, so components render backend messages (including `409` conflicts) instead of inventing their own.
  - `frontend/src/routes/admin/AcademicHierarchy.jsx` — the admin screen. Presents the five levels as a cascading drill-down: selecting an institute loads its departments, selecting a department loads its semesters, and so on down to classes. A level is disabled/empty until its parent is selected, matching the "filter down the hierarchy, not across it" rule in `CLAUDE.md`. Handles loading, empty, and error states per level, and clears descendant selections when an ancestor selection changes.
  - `frontend/src/components/admin/HierarchyLevel.jsx` — one reusable level column: the item list with select/edit/delete affordances, an inline create form, a pending state while a request is in flight, and an inline error line. The five levels differ only in their fields, so this component is driven by a field descriptor rather than duplicated five times.
  - `frontend/src/components/admin/ConfirmDialog.jsx` — a small confirmation prompt used before any delete, so destructive actions are never one accidental click.

- **Modify:**
  - `frontend/src/App.jsx` — add `/admin/academics` wrapped in `<ProtectedRoute role="admin">`.
  - `frontend/src/routes/AdminPortal.jsx` — add a link into the academic hierarchy screen. Keep the rest of the portal placeholder; user management belongs to its own spec.
  - `frontend/src/index.css` — only if shared styling is genuinely needed. Do not introduce a CSS framework or component library.

## Backend

- **Create:**
  - `backend/academic/__init__.py`
  - `backend/academic/service.py` — all hierarchy business logic, with **no Flask request/response objects in any signature** (mirroring `auth/service.py`):
    - domain exceptions: `NotFoundError`, `DuplicateError`, `ValidationError`, `HasChildrenError`
    - `list_*(db, parent_id)` / `create_*(db, ...)` / `update_*(db, id, ...)` / `delete_*(db, id)` for each of the five levels
    - parent-existence verification before every create
    - child-existence verification before every delete (a `count_documents(..., limit=1)` against the child collection)
    - `DuplicateKeyError` → `DuplicateError` translation
  - `backend/academic/validators.py` — shared input validation helpers: `parse_object_id(value)`, `require_non_empty_string(body, field)`, `parse_date(value)`, and the semester `end_date > start_date` check. Keeps the same rules from being rewritten per level.
  - `backend/academic/serializers.py` — document → JSON-safe dict conversion (ObjectId → `str`, `datetime` → ISO string) for each level. One place decides what leaves the backend.
  - `backend/routes/academic.py` — the `academic_bp` blueprint holding all 20 endpoints. Handlers stay thin: check role via `role_required`, pull and validate input, call the service, map domain exceptions to status codes, serialize, return. A blueprint-level `PyMongoError`/`RuntimeError` handler returns a generic `500` exactly as `routes/auth.py` does.

- **Modify:**
  - `backend/app.py` — register `academic_bp`. No other change.
  - `CLAUDE.md` — update the "Implemented vs stub features" table row for *Academic hierarchy* to describe what now exists, and note what is still deferred (faculty assignment, enrollment, user management).

## Files to change

- `backend/app.py`
- `CLAUDE.md`
- `frontend/src/App.jsx`
- `frontend/src/routes/AdminPortal.jsx`
- `frontend/src/index.css` (only if shared styles are required)

## Files to create

- `backend/academic/__init__.py`
- `backend/academic/service.py`
- `backend/academic/validators.py`
- `backend/academic/serializers.py`
- `backend/routes/academic.py`
- `frontend/src/api/academic.js`
- `frontend/src/routes/admin/AcademicHierarchy.jsx`
- `frontend/src/components/admin/HierarchyLevel.jsx`
- `frontend/src/components/admin/ConfirmDialog.jsx`

## New dependencies

**No new dependencies.** `pymongo`, `Flask`, and `Flask-JWT-Extended` cover the backend; `react-router-dom` and the existing `apiFetch` wrapper cover the frontend. Do not add a validation library (`marshmallow`, `pydantic`), an ODM (`mongoengine`, `flask-mongoengine`), a UI kit, or a data-fetching library — the validation surface here is small and explicit.

## Rules for implementation

- **Every endpoint is guarded on the backend** with `role_required` from `auth/decorators.py`. Writes are `admin` only; reads are `admin, faculty`. `ProtectedRoute` and hidden UI are conveniences — an authenticated faculty or student calling a write endpoint directly must get `403`.
- **Import collection names from `database/schema.py`** (`INSTITUTES`, `DEPARTMENTS`, `SEMESTERS`, `COURSES`, `CLASSES`, `CLASS_ENROLLMENTS`). No hardcoded `"departments"` string literals anywhere.
- **The hierarchy is strictly top-down.** A create must verify its parent exists (`404` if not); a list must be scoped by its parent id; queries filter down the hierarchy, never across it. No endpoint returns, say, all classes in the database irrespective of parent.
- **Deletes are blocked, not cascading.** Deleting a node that still has children returns `409` with a message naming the blocker (e.g. `"Cannot delete: 3 departments belong to this institute"`). Silent cascade deletion of an academic subtree is unacceptable — it would take attendance history with it once attendance exists.
- **Keep business logic out of route handlers**, per `CLAUDE.md`. `routes/academic.py` translates HTTP ↔ domain; `academic/service.py` owns integrity rules and holds no Flask objects.
- **Do not duplicate the five levels five times.** The levels share almost all their logic — factor the common create/update/delete/list behavior (parent check, duplicate translation, child check) rather than copy-pasting, while keeping each level's field rules explicit and readable. Avoid the opposite failure too: no metaprogrammed generic resource framework.
- **Never hardcode academic data.** No seed institutes, departments, or sample courses in application code, config, or migrations. The only way data enters these collections is through these endpoints.
- **Faculty assignment to classes is out of scope.** `classes.faculty_id` remains `null`; assigning it requires an endpoint that lists faculty users, which belongs to the Admin User Management spec. Do not build a user-listing endpoint here.
- **Student enrollment (`class_enrollments`) is out of scope** for the same reason — it requires student accounts that only user management can create. This feature only *reads* `class_enrollments` to decide whether a class delete is blocked.
- **No re-parenting.** `PUT` updates a document's own fields only.
- **Validate ObjectIds before querying.** A malformed id must return `400`, never a raw `bson.errors.InvalidId` traceback or a `500`.
- **Dates are BSON dates.** Parse ISO strings into `datetime` on the way in and serialize back to ISO on the way out; never store date strings, per the `schema.py` note.
- **Trim strings on write** and reject blank/whitespace-only `name`/`code` with `400`, so the `minLength: 1` validator is never the first line of defense.
- **Never leak internals.** Validator failures, `DuplicateKeyError` details, and driver exceptions must be translated into the clean JSON contract above; the raw MongoDB error text does not reach the client. Log the exception server-side instead.
- **Preserve existing functionality.** `/api/health`, `flask init-db`, `flask create-admin`, all auth endpoints, and the `01`–`03` test suites must continue to work unchanged. The app must still start when MongoDB is unreachable.
- **Do not touch attendance, face-recognition, or ML code**, and do not create their collections.
- **Tests must not use real production credentials or a production database**, and must not seed realistic institutional data.

## Definition of done

- [ ] `python app.py` starts with `academic_bp` registered, and `/api/health` still responds `200`.
- [ ] As an admin, an institute can be created, listed, updated, and deleted end to end through the API, with `201` on create and the created document returned.
- [ ] The full chain works top-down: institute → department → semester → course → class each create successfully against a real parent id.
- [ ] Creating any child with a non-existent parent id returns `404`; with a malformed parent id returns `400` — never a `500` or a traceback.
- [ ] `GET /api/departments` without `institute_id` returns `400`; with a valid `institute_id` it returns only that institute's departments and no others (verified with two institutes populated).
- [ ] Creating a duplicate within the same parent (same department `code` under one institute, same class `name` under one course) returns `409`, while the same value under a *different* parent succeeds.
- [ ] Creating a semester with `end_date` earlier than or equal to `start_date` returns `400`; a valid semester stores `start_date`/`end_date`/`created_at` as BSON dates (verified in MongoDB, e.g. `typeof` is `date`, not `string`).
- [ ] Deleting an institute that has departments returns `409` and the institute still exists; deleting it after its departments are removed succeeds with `200`.
- [ ] The same delete-blocking behavior holds for department→semester, semester→course, course→class, and class→`class_enrollments`.
- [ ] `PUT` on any level updates only that document's own fields; sending a different parent id in the body does not re-parent it.
- [ ] Every response body exposes ids as strings and dates as ISO strings — no raw `ObjectId(...)` or BSON artifacts appear in JSON.
- [ ] An authenticated **faculty** token can `GET` all five list endpoints but receives `403` on every `POST`, `PUT`, and `DELETE`.
- [ ] An authenticated **student** token receives `403` on the hierarchy endpoints, and an unauthenticated request receives `401`.
- [ ] A `POST`/`PUT`/`DELETE` with valid cookies but a missing `X-CSRF-TOKEN` header is rejected, confirming CSRF protection still applies to the new routes.
- [ ] In the Admin Portal, `/admin/academics` renders the cascading levels: picking an institute loads its departments, and changing the institute clears the previously selected department, semester, course, and class.
- [ ] Creating, renaming, and deleting an item from the UI updates the list without a full page reload, and a delete asks for confirmation first.
- [ ] A backend `409` (duplicate or delete-blocked) surfaces in the UI as the server's message, not a generic failure.
- [ ] No institute, department, semester, course, or class name appears as a hardcoded literal anywhere in the frontend or backend.
- [ ] `classes.faculty_id` is `null` for every class created by this feature, and no faculty-assignment or enrollment endpoint exists yet.
- [ ] `CLAUDE.md`'s "Implemented vs stub features" table reflects the new Academic hierarchy status.
- [ ] The existing `01`, `02`, and `03` test suites pass unchanged.
