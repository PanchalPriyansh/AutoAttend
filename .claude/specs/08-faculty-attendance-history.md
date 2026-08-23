# Spec: Faculty Attendance History

## Overview

`07-attendance-capture` gave faculty a way to record a lecture, but no way to look at one again. Once a session is saved it becomes write-only: there is no list of what has been taken, no way to open a past date, and the only way to correct a mistake is to re-capture the room and save with `replace: true` — which is impossible the day after, when the students have gone. A faculty member who marked the wrong student present, or who took attendance against the wrong class, currently has no remedy inside the application at all.

This feature adds the other half of the register: for a class they hold, faculty can list the sessions taken, open one, correct the present/absent list, and delete a session that should never have existed. It is deliberately the *history* of what was recorded — reading, correcting, and removing sessions the capture flow already created. It computes no percentages, shows nothing to students, and predicts nothing: attendance rates and the student's own view are `09`, risk prediction is `10`, and low-attendance email is `11`. It introduces no new collection, no new dependency, and no computer vision — the whole feature works on a server where neither `dlib` nor OpenCV is built.

Because this is the first feature that changes attendance **after** the fact, it also carries the audit obligation that comes with that: an edited session must record that it was edited and by whom, and correcting a record must be distinguishable from having recorded it that way in the first place.

## Depends on

- `01-project-foundation` — `create_app()`, blueprint registration, React routing shell.
- `02-database-setup` — `database/schema.py` as the single declaration point for collections/validators/indexes, and `flask init-db` (whose `collMod` pass is what applies a changed validator to an existing collection).
- `03-authentication` — `role_required(*roles)` (`backend/auth/decorators.py`), HttpOnly-cookie JWT + CSRF, `requestJson` (`frontend/src/api/client.js`), `AuthContext`, `ProtectedRoute`.
- `04-academic-hierarchy-management` — the `classes` collection and the hierarchy names used to label a class.
- `05-admin-user-management` — `classes.faculty_id`, the `class_enrollments` roster, `users.assignments.list_enrollments`, and the `common/errors.py` + `common/validators.py` + `common/http.py` + `common/serializers.py` shared layer.
- `07-attendance-capture` — everything this feature reads and edits: the `attendance_sessions` / `attendance_records` collections, `attendance/service.py` (`require_owned_class`, `_load_session_records`), `attendance/validators.py` (`require_records`, `require_status`, `require_marked_by`, `parse_attendance_date`), `attendance/serializers.py` (`serialize_session`), `attendance/errors.py` (`ForbiddenError`), the `attendance_bp` blueprint and its error handlers, `frontend/src/api/attendance.js`, and `/faculty/attendance`.

## APIs

All four endpoints are added to the existing `attendance_bp` blueprint (`url_prefix="/api"`) — a second blueprint for the same resource would split its error handlers. **Every endpoint is `faculty` only, and every one additionally requires that the target class's `faculty_id` is the acting faculty member**, exactly as `07` established. For the three session-scoped endpoints the class is reached through the session, so an unknown session id and someone else's session must be told apart carefully (see the rules).

- `GET /api/attendance/sessions?class_id=…&from=YYYY-MM-DD&to=YYYY-MM-DD&limit=&skip=` — sessions recorded for one owned class, newest first. `from`/`to` are optional and inclusive; `limit` defaults to `50` and is capped at `200`; `skip` defaults to `0` — **faculty (owner)**
- `GET /api/attendance/sessions/<session_id>` — one saved session with its records — **faculty (owner)**
- `PUT /api/attendance/sessions/<session_id>` — correct the present/absent list of a saved session; JSON — **faculty (owner)**
- `DELETE /api/attendance/sessions/<session_id>` — delete a session and its records — **faculty (owner)**

The existing `GET /api/attendance/session?class_id=…&date=…` (singular) stays exactly as it is: it answers "has this lecture been taken yet?" during capture and is looked up by class and date. The new plural collection is addressed by id. Neither replaces the other and neither changes.

**Status codes** — same `{ "error": "..." }` contract as `01`–`07`:

- `200` — successful read, edit, or delete
- `400` — malformed ObjectId, malformed `from`/`to`, a `to` earlier than `from`, a non-integer or negative `limit`/`skip`, a `records` list that does not match the session's students exactly, or an invalid `status` / `marked_by`
- `401` / `403` — unauthenticated / not faculty (`role_required`), **or** faculty who is not the assigned holder of the session's class (`403`)
- `404` — the class does not exist, or the session id does not exist
- `500` — database error, via the blueprint-level `PyMongoError` / `RuntimeError` handlers that already exist

No `409` (nothing here can collide — a session's class and date are immutable) and no `503` (no CV library is touched on any of these paths).

**Session summary shape** — the list rows, deliberately **without** a `records` array:

```json
{
  "id": "...",
  "class_id": "...",
  "date": "2026-08-14",
  "source": "photo",
  "taken_by": "...",
  "created_at": "2026-08-14T09:05:00+00:00",
  "updated_at": "2026-08-15T11:20:00+00:00",
  "updated_by": "...",
  "edited": true,
  "present_count": 38,
  "absent_count": 4,
  "total_count": 42
}
```

- `edited` — `true` when the session has been corrected since it was created. Derived, not stored; a stored flag is a second source of truth that drifts.
- `updated_by` — the faculty member who last changed it, `null` for a session that has never been edited.
- Counts come from an aggregation over `attendance_records` grouped by `session_id`, **not** from loading every record of every session. A semester of full rosters is the payload this list must not carry.

**List response:**

```json
{ "sessions": [ … ], "total": 37, "limit": 50, "skip": 0 }
```

`total` is the count matching the filter, ignoring `limit`/`skip`, so the UI can say what it is showing a page of. The named-key convention (`{ "sessions": [...] }`) matches `04`–`07`.

**Session detail** (`GET .../<id>` and the response to `PUT`) — the existing `serialize_session` output from `07`, extended with the same `updated_by` / `edited` / `total_count` fields. One serializer, one shape; the capture flow's responses gain the new fields too and nothing about their existing fields changes.

**Edit request:**

```json
{ "records": [ { "student_id": "...", "status": "present", "marked_by": "faculty" } ] }
```

Only `records` is accepted. `class_id`, `date`, `source`, `created_at`, and `taken_by` are immutable on edit and are ignored if sent.

**Delete response:** `{ "deleted": true }` with `200`, matching `routes/faces.py`.

## Database changes

**No new collections and no new indexes.**

`attendance_sessions` gains one **optional** field:

| Field | Type | Notes |
|---|---|---|
| `updated_by` | `objectId` | optional; the faculty member who last edited this session. Absent on a session that has only ever been created |

It is added to `properties` in `ATTENDANCE_SESSIONS_VALIDATOR` but **not** to `required`, so every session written by `07` stays valid and no migration or backfill is needed. `flask init-db` applies the changed validator through its existing `collMod` pass.

Why no new index: the list query is an equality on `class_id` plus a range and sort on `date`, which is exactly the prefix shape of the existing `uniq_class_id_date`. Record loads and the cascade delete ride `uniq_session_id_student_id`. Adding an index that duplicates a prefix of an existing one costs writes and buys nothing.

Additional constraints:

- **A record's `class_id` is never rewritten by an edit.** It is denormalised from the parent session and the parent's class cannot change, so the only correct value is the one already stored.
- **Deleting a session deletes its records.** An `attendance_records` row whose `session_id` points at nothing is unreadable data that `09`–`11` would still count. The records go first, then the session, so a failure between the two leaves a session with no records — recoverable and visible — rather than orphaned records nothing can reach.
- **Nothing is soft-deleted.** Users are deactivated rather than deleted because a user is referenced everywhere; an attendance session is referenced by nothing but its own records, and a session taken against the wrong class is not a historical fact worth preserving. A deleted session is gone, which is why the UI must confirm it.

## Frontend

- **Create:**
  - `frontend/src/routes/faculty/AttendanceHistory.jsx` — the `/faculty/attendance/history` screen: pick one of *my* classes, optionally narrow by date range, page through what was recorded, open a session, correct it, delete it.
  - `frontend/src/components/faculty/StudentStatusRow.jsx` — the toggleable present/absent row, extracted verbatim from the `StudentRow` currently defined inside `AttendanceCapture.jsx`. Both screens show the same control over the same domain object; a second copy would drift.
  - `frontend/src/utils/lecture.js` — `today()` and `describeClass()`, also lifted out of `AttendanceCapture.jsx`. Both screens need a max date on a date input and a readable class label, and `today()` in particular has a correctness reason for existing (it is the local calendar day, not the UTC one) that must not be re-derived differently in a second place.

- **Modify:**
  - `frontend/src/api/attendance.js` — add `listAttendanceSessions`, `getAttendanceSessionById`, `updateAttendanceSession`, and `deleteAttendanceSession` in the existing style: unwrap the JSON, let `requestJson` throw the backend's own `error` message.
  - `frontend/src/routes/faculty/AttendanceCapture.jsx` — import `StudentStatusRow`, `today`, and `describeClass` instead of defining them; add a link to the history screen. Behaviour is unchanged — this is the extraction, not a redesign.
  - `frontend/src/App.jsx` — add `/faculty/attendance/history` wrapped in `<ProtectedRoute role="faculty">`.
  - `frontend/src/routes/FacultyDashboard.jsx` — add a second link, beside the existing "Take Attendance" one.

`ConfirmDialog` (`frontend/src/components/admin/ConfirmDialog.jsx`) is reused as-is for the delete confirmation. It is generic despite living under `admin/`; moving it is a rename touching three unrelated admin screens and is out of scope here — copying it is not.

The history UI must:

- refuse to list anything until a class is chosen, and show an empty-state message rather than an error when a class has no sessions yet;
- show each row's date, source, present/absent counts, and an explicit "edited" marker when `edited` is true;
- open a session's full roster with each student's stored status, and let faculty flip any row before saving;
- send `marked_by: "faculty"` for a row that was flipped and preserve the stored `marked_by` for a row that was not — an untouched `"recognition"` record must not become `"faculty"` merely because the session was opened and re-saved;
- disable the save button until something has actually changed, so an accidental open-and-save does not stamp an edit on an untouched session;
- confirm a delete through `ConfirmDialog`, naming the class and date, and state that it cannot be undone;
- state on the edit view that a correction replaces what was recorded and is attributed to the person making it;
- surface a backend `400`/`403`/`404` as the server's own message.

## Backend

- **Create:** no new modules. Every layer this feature needs already exists in `backend/attendance/`; adding a `history.py` beside `service.py` would split one resource's logic across two files with no boundary between them.

- **Modify:**
  - `backend/database/schema.py` — add the optional `updated_by` property to `ATTENDANCE_SESSIONS_VALIDATOR`. Nothing else; no new collection, no new index, no change to `required`.
  - `backend/attendance/validators.py` — add `parse_session_filters` (optional `from`/`to` normalised to UTC midnight like `parse_attendance_date`, with `to` required to be on or after `from`, plus a bounded `limit`/`skip`), and generalise `require_records` so its "not enrolled in this class" message can instead say "not part of this session" when it is validating an edit. One roster-completeness rule, two callers — a second near-identical validator is the defect to avoid here.
  - `backend/attendance/service.py` — add `list_sessions`, `get_session_by_id`, `update_session_records`, and `delete_session`. All four go through the existing `require_owned_class`; the three session-scoped ones reach the class through the session document. `_load_session_records` is reused unchanged.
  - `backend/attendance/serializers.py` — add `serialize_session_summary` (counts, no records) and `serialize_session_summaries`, and extend `serialize_session` with `updated_by`, `edited`, and `total_count`. Both keep building from explicit field allow-lists.
  - `backend/routes/attendance.py` — add the four handlers to the existing blueprint, thin exactly like the ones already there. No new error handlers are needed: `ValidationError`, `ForbiddenError`, `NotFoundError`, `PyMongoError`, and `RuntimeError` are all already mapped.
  - `frontend/src/api/attendance.js`, `frontend/src/routes/faculty/AttendanceCapture.jsx`, `frontend/src/App.jsx`, `frontend/src/routes/FacultyDashboard.jsx` — as described above.
  - `CLAUDE.md` — mark faculty attendance history implemented in the "Implemented vs stub features" table, update the attendance row's deferred list (history, editing, and deletion are no longer deferred), and record what this feature defers.

## Files to change

- `backend/database/schema.py`
- `backend/attendance/validators.py`
- `backend/attendance/service.py`
- `backend/attendance/serializers.py`
- `backend/routes/attendance.py`
- `CLAUDE.md`
- `frontend/src/api/attendance.js`
- `frontend/src/routes/faculty/AttendanceCapture.jsx`
- `frontend/src/routes/FacultyDashboard.jsx`
- `frontend/src/App.jsx`

## Files to create

- `frontend/src/routes/faculty/AttendanceHistory.jsx`
- `frontend/src/components/faculty/StudentStatusRow.jsx`
- `frontend/src/utils/lecture.js`

## New dependencies

No new dependencies. This feature is reads, edits, and deletes over collections that already exist — nothing here needs a library the project does not already have, and `scikit-learn` (`10`) and SMTP (`11`) must not appear.

## Rules for implementation

- **Every endpoint is guarded on the backend** with `role_required("faculty")` **and** `require_owned_class`. An authenticated faculty member acting on another faculty member's session receives `403` on all four endpoints. Hidden UI is not authorization, and neither is a session id being hard to guess.
- **Resolve the session before the class, and answer the honest status.** A session id that does not exist is `404`; a session that exists but belongs to someone else's class is `403`. Do not collapse the second into the first — the caller is a trusted, authenticated faculty member, and telling them plainly that a class is not theirs is the behaviour `07` already established for `/api/attendance/session`.
- **Sessions are listed for one class at a time.** `class_id` is required on the list endpoint. A cross-class "everything I ever took" feed would need a different ownership check written from scratch; requiring the class keeps exactly one ownership rule in the feature.
- **The list must not load records.** Counts come from a single grouped aggregation over `attendance_records` for the page's session ids. Loading every record of every session to count them is the query that makes this screen unusable by the second semester.
- **`limit` and `skip` are bounded and validated.** A missing `limit` defaults to `50`, a `limit` above `200` is clamped or rejected — never honoured, and never passed to MongoDB unchecked. Negative or non-integer values are `400`.
- **An edit changes statuses, nothing else.** `class_id`, `date`, `source`, `created_at`, and `taken_by` are immutable; `updated_at` and `updated_by` are the only session fields an edit touches. `taken_by` in particular stays the person who originally recorded the lecture — overwriting it would erase who took it. (`replace: true` on the capture route legitimately updates `taken_by` and `source`, because re-capturing genuinely re-takes the lecture. Editing is not that, and must not be implemented by calling it.)
- **The authoritative set for an edit is the session's existing records, not the current roster.** The payload must account for exactly the students already in the session — everyone once, nobody twice, nobody new. A student enrolled after the lecture was taken must not appear in it, and a student unenrolled since must not vanish from it: the session records who was considered *that day*, and re-deriving it from today's roster would silently rewrite history.
- **`updated_by` comes from the JWT identity**, never from the request body — the same rule `07` set for `taken_by`.
- **`marked_by` provenance is preserved through an edit.** A record the client did not change keeps its stored value; a record whose status changed is `"faculty"`. The client supplies it and the backend validates it against the enum, but it is never used to decide what a caller is allowed to do. A blanket rewrite of every record to `"faculty"` on save would destroy the only signal available for judging how well recognition actually performs.
- **Validate the whole payload before writing anything.** As in `07`'s `replace` path, a malformed edit must not have already deleted the records it was going to replace.
- **Delete removes records first, then the session.** Both are logged server-side with the class, date, and acting faculty member — never with student names or anything derived from a capture.
- **Import collection names from `database/schema.py`** (`ATTENDANCE_SESSIONS`, `ATTENDANCE_RECORDS`, `CLASSES`, `USERS`). No hardcoded collection-name literal anywhere else.
- **Keep business logic out of route handlers.** `routes/attendance.py` translates HTTP ↔ domain; `attendance/service.py` owns the rules and holds no Flask objects.
- **Reuse before rewriting.** `require_owned_class`, `_load_session_records`, `require_records`, `require_status`, `require_marked_by`, `parse_object_id`, `json_body`, `student_summary`, `serialize_session`, the blueprint's error handlers, `ConfirmDialog`, and `requestJson` all exist. A second ownership check, a second roster-completeness validator, a second confirm dialog, or a second session serializer is a defect.
- **No CV import anywhere in this feature.** Nothing here decodes an image or a video, and every endpoint must work on a server with neither `dlib` nor OpenCV installed. If a history endpoint can return `503`, something has been imported that should not have been.
- **No media, ever.** `07` never stored the capture and this feature must not introduce anywhere to put one — no thumbnail on a session row, no "view the photo" link, no field for it. There is nothing to show, by design.
- **Never leak internals.** Driver exceptions and `$jsonSchema` validator failures are translated into the clean JSON contract and logged server-side.
- **Never hardcode academic data.** Classes, rosters, and student identities come from MongoDB.
- **Preserve existing functionality.** `/api/health`, `flask init-db`, `flask create-admin`, every auth endpoint, all twenty academic endpoints, all ten user-management endpoints, all five face-enrolment endpoints, all four attendance-capture endpoints, `/admin/academics`, `/admin/users`, `/admin/face-enrollment`, and `/faculty/attendance` must keep working. `flask init-db` must stay idempotent, and the `AttendanceCapture.jsx` extraction must leave capture behaving exactly as it did — same rows, same toggling, same `marked_by` attribution, same replace flow.
- **Do not build what `09`–`11` own:** no attendance percentages or rates, no thresholds, no charts or trends, no student-facing view, no admin access to attendance, no risk prediction, no email. A present/absent count for one session is this feature's; an attendance rate across sessions is `09`'s.
- **Deferred deliberately, to be recorded in `CLAUDE.md` rather than solved here:** admins still cannot view or edit attendance; there is no cross-class or institute-wide history view; there is no export (CSV/PDF); there is no audit *trail* beyond `updated_by` and `updated_at` — a session records who last edited it, not a history of every edit; and a deleted session is unrecoverable.
- **Tests must not use real production credentials, a production database, real personal data, or any real biometric data.** Sessions and records are seeded directly; no capture, no encoder, and no media fixtures are needed anywhere in this feature's tests.

## Definition of done

- [ ] `flask init-db` applies the updated `attendance_sessions` validator on an existing database, is idempotent on re-run, and leaves sessions written by `07` (which have no `updated_by`) valid and readable.
- [ ] `python app.py` starts; `/api/health`, the auth endpoints, the twenty academic endpoints, the ten user-management endpoints, the five face-enrolment endpoints, and the four attendance-capture endpoints all still respond as before.
- [ ] `GET /api/attendance/sessions` returns that class's sessions newest-first with correct `present_count` / `absent_count` / `total_count`, and returns an empty list plus `total: 0` (not a `404`) for an owned class with no sessions.
- [ ] The list response contains **no** `records` array, and the endpoint issues a bounded number of queries regardless of how many sessions are returned — verified by seeding many sessions and asserting counts are aggregated, not per-session loaded.
- [ ] `from` / `to` filter inclusively on the lecture date; a `to` earlier than `from`, a malformed date, a negative `skip`, and a non-integer `limit` each return `400`.
- [ ] `limit` defaults to 50, never returns more than 200 rows however large a `limit` is requested, and `skip` pages through without duplicating or dropping a session; `total` stays the unpaged count.
- [ ] `GET /api/attendance/sessions/<id>` returns the session with every stored record and its student, including explicit `"absent"` rows.
- [ ] A session that has never been edited reports `edited: false` and `updated_by: null`; after an edit it reports `edited: true` with `updated_by` equal to the acting faculty member and a later `updated_at`.
- [ ] `PUT /api/attendance/sessions/<id>` flips a student's status, leaves `class_id`, `date`, `source`, `created_at`, and `taken_by` unchanged, and still stores exactly one record per student in the session.
- [ ] A record whose status was changed stores `marked_by: "faculty"`; a record submitted unchanged keeps its stored `marked_by`, including `"recognition"`.
- [ ] An edit payload missing a student, containing a duplicate, naming a student who is not in the session, or carrying an invalid `status` / `marked_by` each return `400` and change nothing — verified by re-reading the session afterwards.
- [ ] An edit is validated against the **session's** students, not the current roster: enrolling a new student in the class does not make an otherwise-valid edit fail, and unenrolling one does not make it require their removal.
- [ ] `DELETE /api/attendance/sessions/<id>` returns `{"deleted": true}` and leaves neither the session nor any of its `attendance_records` rows behind; a subsequent `GET` of that id returns `404`.
- [ ] Deleting one session leaves other sessions for the same class, and their records, untouched.
- [ ] After a delete, the same class and date can be recorded again through `POST /api/attendance` without a `409`.
- [ ] A faculty member acting on a class or session belonging to **someone else** receives `403` on all four endpoints; a class with no assigned faculty is likewise `403`, not `500`.
- [ ] A non-existent session id returns `404`, and a malformed session id returns `400` — neither returns `500`.
- [ ] An authenticated **admin** token and an authenticated **student** token each receive `403` on all four endpoints, and an unauthenticated request receives `401`.
- [ ] `PUT` and `DELETE` with valid cookies but no `X-CSRF-TOKEN` header are rejected, confirming CSRF still applies to the new mutating routes.
- [ ] With neither `dlib` nor OpenCV installed, all four endpoints work normally — none returns `503`, and no CV module is imported on any of their code paths.
- [ ] `/faculty/attendance/history` lists the faculty member's own classes, filters by date range, pages, opens a session, flips rows, saves, and deletes behind a confirmation — each step updating without a full page reload.
- [ ] The history screen shows an edited session as edited, disables save until something changes, and surfaces a backend `400`/`403`/`404` as the server's own message.
- [ ] `/faculty/attendance` still selects a class and date, captures by file upload and by webcam, shows the three groups, flips rows with the correct `marked_by` attribution, and saves and replaces exactly as before the `StudentStatusRow` / `lecture.js` extraction.
- [ ] The webcam stream still stops when capture ends or the capture component unmounts.
- [ ] `CLAUDE.md` reflects the new Faculty attendance history status, the attendance row's reduced deferred list, and this feature's own deferred items.
