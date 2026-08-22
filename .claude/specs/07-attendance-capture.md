# Spec: Attendance Capture

## Overview

This is the feature the whole project exists for. `06-face-enrollment` taught the system what each student looks like, but nothing yet consumes those encodings: a faculty member today still has no way to record who attended a lecture. This feature closes the loop — faculty pick one of their own classes and a date, capture the room as a photo or a short video, and the recognition pipeline matches every face it finds against the encodings of the students on that class's roster. The result is a **proposal**, not a verdict: present, absent, unrecognisable (no samples enrolled), and a count of faces belonging to nobody on the roster. Faculty review that proposal, correct anything wrong, and only then is an attendance session written to MongoDB.

It is the first feature to persist attendance, so it owns the `attendance_sessions` / `attendance_records` collections that `08`–`11` will read, and the first to need video, so it owns the introduction of `opencv-python` and the frame-extraction boundary. It is deliberately the *capture* half only: browsing or editing past attendance is `08`, the student's own view is `09`, risk prediction is `10`, and low-attendance email is `11`.

## Depends on

- `01-project-foundation` — `create_app()`, blueprint registration, env-driven `Config`, React routing shell.
- `02-database-setup` — `database/schema.py` as the single declaration point for collections/validators/indexes, and `flask init-db`.
- `03-authentication` — `role_required(*roles)` (`backend/auth/decorators.py`), HttpOnly-cookie JWT + CSRF, `requestJson` (`frontend/src/api/client.js`) including its `FormData` handling, `AuthContext`, `ProtectedRoute`.
- `04-academic-hierarchy-management` — the `classes` collection and the `Institute → … → Class` chain used to describe a class on screen.
- `05-admin-user-management` — `classes.faculty_id` (which is what makes "my classes" answerable), the `class_enrollments` roster, `users.assignments.list_enrollments`, and the `common/errors.py` + `common/validators.py` + `common/http.py` shared layer.
- `06-face-enrollment` — the `face_encodings` collection, `recognition/encoder.py` (`is_available`, `encode_faces`, `closest_match`, `MATCH_TOLERANCE`, `DETECTOR_MODEL`), `recognition/validators.py` (`require_image`, `MAX_REQUEST_BYTES`), and the lazy-import discipline that keeps the app runnable without `dlib`.

## APIs

All endpoints live on a new `attendance_bp` blueprint (`url_prefix="/api"`). **Every endpoint is `faculty` only, and every one that names a class additionally requires that `classes.faculty_id` equals the acting faculty member's id.** `role_required` cannot express that second rule — it is a per-document ownership check that belongs in the service, and it must be applied on every route below, not just on save.

- `GET /api/classes/assigned` — the classes assigned to the acting faculty member, each with its course/semester/department/institute names so it can be labelled without five extra requests — **faculty**
- `POST /api/attendance/recognize` — run the recognition pipeline; `multipart/form-data` with `class_id` and exactly one of an `image` or a `video` file part. Returns a proposal and **writes nothing to the database** — **faculty (owner)**
- `GET /api/attendance/session?class_id=…&date=YYYY-MM-DD` — the existing session for that class and date, with its records, or `404` when attendance has not been taken yet — **faculty (owner)**
- `POST /api/attendance` — save a reviewed session; JSON — **faculty (owner)**

**Status codes** — same `{ "error": "..." }` contract as `01`–`06`:

- `200` — successful read or save-by-replace
- `201` — a new session was created
- `400` — malformed ObjectId or date; a future date; missing/both/unknown media parts; an unreadable image or video; a `records` list that does not match the roster exactly; an invalid `status`, `source`, or `marked_by`
- `401` / `403` — unauthenticated / not faculty (`role_required`), **or** faculty who is not the assigned holder of that class (`403`)
- `404` — the class does not exist, or `GET /api/attendance/session` finds no session for that class and date
- `409` — a session already exists for that class and date and `replace` was not set
- `413` — the uploaded media exceeds the request size limit
- `500` — database error, via blueprint-level `PyMongoError` / `RuntimeError` handlers mirroring `routes/faces.py`
- `503` — the face-recognition library, or (for a video) OpenCV, is not installed on the server

**Assigned-class shape:**

```json
{
  "id": "...",
  "name": "CS-A",
  "course": "Data Structures",
  "semester": "Semester 3",
  "department": "Computer Engineering",
  "institute": "Example Institute",
  "student_count": 42
}
```

**Recognition proposal shape** — the roster is partitioned into three disjoint lists that together account for every enrolled student:

```json
{
  "class_id": "...",
  "source": "photo",
  "frames_analyzed": 1,
  "detected_faces": 12,
  "unknown_faces": 2,
  "recognized": [
    { "student": { "id": "...", "name": "...", "email": "...", "is_active": true },
      "distance": 0.412, "confidence": "high" }
  ],
  "unrecognized": [ { "student": { … }, "sample_count": 3 } ],
  "not_enrolled": [ { "student": { … }, "sample_count": 0 } ]
}
```

- `recognized` — matched within tolerance; proposed **present**.
- `unrecognized` — has face samples but was not matched; proposed **absent**.
- `not_enrolled` — has **no** face samples, so the pipeline was structurally incapable of finding them. Proposed absent, but presented separately: marking such a student absent because nobody ever enrolled their face is a data-entry failure, not an attendance fact, and faculty must see the difference.
- `unknown_faces` — faces detected that matched nobody on the roster. Surfaced as a review prompt (a visitor, a passer-by, or a student whose enrolment is poor), never auto-assigned to anyone.
- `confidence` — `"high"` or `"low"`, derived from where the distance falls relative to the tolerance. A band, not a probability.

**Save request:**

```json
{
  "class_id": "...",
  "date": "2026-08-14",
  "source": "photo",
  "replace": false,
  "records": [ { "student_id": "...", "status": "present", "marked_by": "recognition" } ]
}
```

**Saved-session shape** (returned by `POST /api/attendance` and `GET /api/attendance/session`):

```json
{
  "id": "...",
  "class_id": "...",
  "date": "2026-08-14",
  "source": "photo",
  "taken_by": "...",
  "created_at": "2026-08-14T09:05:00+00:00",
  "updated_at": "2026-08-14T09:05:00+00:00",
  "present_count": 38,
  "absent_count": 4,
  "records": [
    { "student": { "id": "...", "name": "...", "email": "...", "is_active": true },
      "status": "present", "marked_by": "recognition" }
  ]
}
```

List endpoints return their array under a named key (`{ "classes": [...] }`), matching the `04`–`06` convention.

## Database changes

**Two new collections**, declared in `backend/database/schema.py` alongside the existing eight so `flask init-db` creates them with their validators and indexes. Nothing else in the project may create a collection.

`attendance_sessions` — one document per class per date:

| Field | Type | Notes |
|---|---|---|
| `class_id` | `objectId` | required |
| `date` | `date` | required; the lecture date normalised to **UTC midnight**, so one calendar day is exactly one value and the unique index below actually holds |
| `source` | `string` | required; `"photo"`, `"video"`, or `"manual"` — how the session was produced |
| `taken_by` | `objectId` | required; the faculty member, from the JWT identity |
| `created_at` | `date` | required |
| `updated_at` | `date` | required |

Indexes: unique `uniq_class_id_date` on `(class_id, date)` — the database, not just application code, is what prevents two sessions for one lecture.

`attendance_records` — one document per student per session:

| Field | Type | Notes |
|---|---|---|
| `session_id` | `objectId` | required |
| `class_id` | `objectId` | required; denormalised from the session so `09`/`10` can query one student's attendance in one class without a join. Must always equal the parent session's `class_id` |
| `student_id` | `objectId` | required |
| `status` | `string` | required; `"present"` or `"absent"` |
| `marked_by` | `string` | required; `"recognition"` or `"faculty"` — whether the pipeline proposed this status or a human set it. Provenance for later review, never an authorization signal |
| `created_at` | `date` | required |

Indexes: unique `uniq_session_id_student_id` on `(session_id, student_id)`; non-unique `idx_student_id_class_id` on `(student_id, class_id)` for the per-student queries `09`–`11` will make.

Additional constraints:

- **No recognition counts are stored.** `detected_faces` / `unknown_faces` are review aids computed at capture time; persisting numbers the client hands back would be storing an unverifiable claim as if it were a fact.
- **The classroom image or video is never stored** — not in MongoDB, not on disk, not in a temp file that outlives the request, not in a log. This is the same rule `06` set for enrolment photos, and it matters more here: a classroom frame contains many people who never consented to a photograph being kept.
- **A record per student, not an array on the session.** A roster is bounded, but `09`–`11` query by student across sessions, and an embedded array would force every such query through the whole session document.
- Absent students are stored **explicitly**, not inferred from missing rows. "Absent" and "attendance was never taken" are different facts and must stay distinguishable.

## Frontend

- **Create:**
  - `frontend/src/api/attendance.js` — wrappers over the four endpoints, following `frontend/src/api/faces.js`: unwrap the JSON, let `requestJson` throw the backend's own `error` message. The recognise call sends `FormData` and must not set `Content-Type` itself.
  - `frontend/src/routes/faculty/AttendanceCapture.jsx` — the `/faculty/attendance` screen: choose one of *my* classes and a date, capture, review the proposal, save.
  - `frontend/src/components/faculty/ClassroomCapture.jsx` — the capture control: a file input accepting an image **or** a video, plus a webcam still. Deliberately not `FaceCapture.jsx`, whose accepted types, ids, and "one face only" guidance are all wrong for a room full of people; the camera lifecycle they do share is extracted rather than copied (below).
  - `frontend/src/hooks/useCamera.js` — the `getUserMedia` lifecycle both capture controls need: start, stop **every** track, and release on unmount so the camera indicator never stays lit.

- **Modify:**
  - `frontend/src/components/admin/FaceCapture.jsx` — use `useCamera` instead of its own inline stream handling. Behaviour is unchanged; this is the extraction, not a redesign.
  - `frontend/src/App.jsx` — add `/faculty/attendance` wrapped in `<ProtectedRoute role="faculty">`.
  - `frontend/src/routes/FacultyDashboard.jsx` — link to the new screen. It is currently a name and a logout button; a link is the whole change.

The review UI must:

- show the three groups as separate, labelled sections — a `not_enrolled` student must never be silently mixed in with a student the camera genuinely failed to find;
- let faculty flip any student between present and absent, including a recognised one, and track that a flipped record's `marked_by` becomes `"faculty"`;
- show `unknown_faces` as a plain warning when it is non-zero;
- mark `"low"` confidence matches visibly so a weak match gets looked at;
- state that recognition is assistive and the saved list is the faculty member's own record;
- refuse to save until a class and date are chosen, and surface a `409` as an explicit "attendance already exists for this date — replace it?" step rather than a generic failure.

## Backend

- **Create:**
  - `backend/recognition/frames.py` — the **only** module that imports `cv2`. Video bytes in, a bounded list of evenly-sampled frame images out. Lazy import and `is_available()` exactly as `encoder.py` does it, so a machine without OpenCV still starts, still serves photos, and reports `503` only for video. The frame cap, the allowed video content types, and the size ceiling live here as named constants.
  - `backend/recognition/matcher.py` — the pipeline as pure numeric logic: given the encodings detected in one or more frames and the roster's known encodings, return per-student best distances, the unknown-face count, and the confidence band. No database, no Flask, no request objects. It may call `encoder.py`; it may not query MongoDB.
  - `backend/attendance/__init__.py`
  - `backend/attendance/errors.py` — only if a genuinely feature-specific exception is needed. Bad input, missing target, and conflict already exist in `common/errors.py` and must be reused. The ownership failure needs a `403`, which no existing exception maps to — that is the one addition this file is likely to justify (e.g. `ForbiddenError`).
  - `backend/attendance/validators.py` — date parsing/normalisation, the `status` / `source` / `marked_by` enums, and the roster-completeness rule for a `records` payload.
  - `backend/attendance/serializers.py` — `serialize_assigned_class`, `serialize_proposal`, `serialize_session`, built from explicit field allow-lists like `recognition/serializers.py`, so no encoding vector and no raw document can reach a response.
  - `backend/attendance/service.py` — the business logic, with no Flask objects in any signature: resolve and ownership-check the class, load the roster and its encodings, drive the matcher, and read/write the two collections.
  - `backend/routes/attendance.py` — the `attendance_bp` blueprint. Thin handlers exactly as `routes/faces.py`: `role_required("faculty")`, pull the parts off the request, validate, call the service, serialize. Exception→status mapping goes in blueprint-level `errorhandler`s.

- **Modify:**
  - `backend/database/schema.py` — add `ATTENDANCE_SESSIONS` and `ATTENDANCE_RECORDS`, their validators, and their indexes to `COLLECTIONS`. Existing declarations are untouched.
  - `backend/recognition/validators.py` — add `require_video` beside `require_image`, and raise `MAX_REQUEST_BYTES` so it clears the video ceiling. The per-media limits stay the thing that produces a `400`; `MAX_CONTENT_LENGTH` stays the thing that produces a `413`.
  - `backend/app.py` — register `attendance_bp`. Nothing else: the `MAX_CONTENT_LENGTH` line already reads `MAX_REQUEST_BYTES`, and the `RequestEntityTooLarge` handler already covers the new routes.
  - `backend/requirements.txt` — add `opencv-python` with a comment noting it is needed only for video capture and is imported lazily.
  - `CLAUDE.md` — mark Attendance capture implemented in the "Implemented vs stub features" table, and record what is deferred.

## Files to change

- `backend/database/schema.py`
- `backend/recognition/validators.py`
- `backend/app.py`
- `backend/requirements.txt`
- `CLAUDE.md`
- `frontend/src/App.jsx`
- `frontend/src/routes/FacultyDashboard.jsx`
- `frontend/src/components/admin/FaceCapture.jsx`

## Files to create

- `backend/recognition/frames.py`
- `backend/recognition/matcher.py`
- `backend/attendance/__init__.py`
- `backend/attendance/errors.py` (only if a feature-specific exception is genuinely needed)
- `backend/attendance/validators.py`
- `backend/attendance/serializers.py`
- `backend/attendance/service.py`
- `backend/routes/attendance.py`
- `frontend/src/api/attendance.js`
- `frontend/src/routes/faculty/AttendanceCapture.jsx`
- `frontend/src/components/faculty/ClassroomCapture.jsx`
- `frontend/src/hooks/useCamera.js`

## New dependencies

- `opencv-python` — decoding a video and sampling frames from it. This is the feature the roadmap always intended it for; it is not used for detection or encoding, which stay with `face_recognition`.

**Not now:** `scikit-learn` belongs to `10` and SMTP/email to `11` — adding either here is the mid-feature dependency creep `CLAUDE.md` prohibits. No task queue, no storage service (nothing is stored), no charting or UI library, no React webcam package (`getUserMedia` is a browser API), and no second recognition stack.

## Rules for implementation

- **Every endpoint is guarded on the backend** with `role_required("faculty")` **and** an explicit check that the class's `faculty_id` matches the caller. An authenticated faculty member calling these endpoints for somebody else's class must receive `403`, whichever endpoint it is. Hidden UI is not authorization.
- **Recognition proposes; faculty decide.** `POST /api/attendance/recognize` writes nothing. No path in this feature saves attendance without a faculty member submitting the reviewed list. This is what `CLAUDE.md` means by never treating face recognition as accurate: the pipeline's output is evidence, and the saved record is a human's statement.
- **Match only against the class roster.** Load encodings for the students enrolled in *this* class, never the whole `face_encodings` collection. A student from another class must be structurally incapable of being marked present here, and the query cost must not grow with the size of the institute.
- **Handle the failure modes explicitly**, per `CLAUDE.md`:
  - No face detected in the media → a valid proposal with everyone unrecognised, not an error; the faculty member may still save an all-absent session or retry with a better capture.
  - A face matching nobody → counted in `unknown_faces`, never attached to the nearest student.
  - One student matched by two different faces → the better (smaller) distance wins and the other face becomes an unknown face. Never mark one student present twice, and never let a duplicate silently mark a second student.
  - A match near the tolerance boundary → `"low"` confidence, surfaced for review rather than hidden.
  - The tolerance and the low-confidence band are named constants with comments stating they are heuristics, not guarantees. Reuse `encoder.MATCH_TOLERANCE`; do not declare a second threshold.
- **The captured image/video is never persisted or logged.** Process in memory, derive encodings, discard the bytes. No `uploads/` directory, no frames written to disk, no base64 copy in Mongo, no media bytes in a log line or an exception message. OpenCV's video reader must be given an in-memory buffer or a temp file that is deleted in a `finally` before the response is returned — if a temp file is unavoidable for `cv2.VideoCapture`, deleting it is not optional and must be covered by a test.
- **All CV code stays inside `backend/recognition/`.** `cv2` may be imported only in `frames.py`; `face_recognition`/`numpy` only in `encoder.py` (and `numpy` in `matcher.py` if the distance work genuinely needs it). No CV import in a route, in `attendance/service.py`, or anywhere in `users/`, `academic/`, or `auth/`.
- **Both libraries are imported lazily and their absence is handled.** The app must still start, `/api/health` must still respond, and the `01`–`06` suites must still pass with neither `dlib` nor OpenCV built. A photo request on such a machine returns `503`; a video request returns `503` when only OpenCV is missing, and the photo path must not be taken down with it.
- **Video is bounded.** A capped number of evenly-sampled frames, a size ceiling, and an allowed content-type list — all named constants. A student recognised in **any** analysed frame is proposed present, taking their best distance across frames; frames are a way to get more chances at the same room, not more people.
- **Validate the upload before decoding it.** Exactly one of `image` / `video` must be present — neither and both are `400`. Enforce content type and size on cheap checks first; a decoder failure is a `400`, never a `500` or a traceback.
- **Dates are normalised and bounded.** Accept `YYYY-MM-DD`, store UTC midnight as a real BSON `date`, and reject a future date with `400`. Attendance for a lecture that has not happened is a typo, not a use case.
- **A `records` payload must match the roster exactly** — every enrolled student exactly once, no duplicates, no student who is not enrolled. A partial list would silently write an incomplete session that later features read as fact.
- **Duplicate sessions are refused, not merged.** A second save for the same class and date returns `409` unless `replace` is explicitly set; `replace` rewrites that session's records and bumps `updated_at`/`taken_by`. The unique index is the backstop, so the `409` must also be produced correctly when two saves race. Because replace is delete-then-insert rather than a transaction, it must not delete the old records until the new ones have been validated.
- **`taken_by` comes from the JWT identity**, never from the request body. `marked_by` is client-supplied provenance for later review and must be validated against its enum, but it is never used to decide what a caller is allowed to do.
- **Import collection names from `database/schema.py`** (`ATTENDANCE_SESSIONS`, `ATTENDANCE_RECORDS`, `FACE_ENCODINGS`, `CLASSES`, `CLASS_ENROLLMENTS`, `USERS`). No hardcoded collection-name literal anywhere else.
- **Keep business logic out of route handlers.** `routes/attendance.py` translates HTTP ↔ domain; `attendance/service.py` owns the rules and holds no Flask objects; `recognition/matcher.py` owns the numeric pipeline and holds no database.
- **Reuse before rewriting.** `common/errors.py`, `common/validators.py`, `common/http.py`, `common/serializers.py`, `role_required`, `users.assignments.list_enrollments`, `recognition/encoder.py`, `require_image`, and `requestJson` all exist. A second ObjectId parser, a second error hierarchy, a second roster join, or a second image validator is a defect.
- **Never leak internals.** Driver exceptions, `$jsonSchema` validator failures, and CV library errors are translated into the clean JSON contract and logged server-side. No encoding vector appears in any response, log, or error message.
- **Never hardcode academic data.** Classes, rosters, and student identities all come from MongoDB.
- **Preserve existing functionality.** `/api/health`, `flask init-db`, `flask create-admin`, every auth endpoint, all twenty academic endpoints, all ten user-management endpoints, all five face-enrolment endpoints, `/admin/academics`, `/admin/users`, and `/admin/face-enrollment` must keep working. `flask init-db` must stay idempotent on a database that already has the first eight collections, and the `FaceCapture.jsx` refactor must leave enrolment behaving exactly as it did.
- **Do not touch ML or notification code**, do not create their collections, and do not build attendance history browsing, editing after the fact, the student view, or any percentage/threshold logic — those are `08`–`11`.
- **Deferred deliberately, to be recorded in `CLAUDE.md` rather than solved here:** admins cannot take or view attendance (faculty-only for now); there is no attendance history, editing, or deletion after the save/replace in this feature; no partial or manual-only session flow beyond the review screen; no live/streaming recognition; and recognition runs synchronously inside the request, so a large video ties up a worker — a queue is the answer if that becomes a real problem, not a bigger timeout.
- **Tests must not use real production credentials, a production database, real personal data, or any real biometric data.** Use synthetic encodings and a monkeypatched/faked encoder, matcher, and frame extractor; no real faces, no photographs or videos of real people, and no media fixtures committed to the repository.

## Definition of done

- [ ] `flask init-db` creates `attendance_sessions` and `attendance_records` with their validators and indexes, is idempotent on re-run, and leaves the existing eight collections unchanged.
- [ ] `python app.py` starts with `attendance_bp` registered; `/api/health`, the auth endpoints, the twenty academic endpoints, the ten user-management endpoints, and the five face-enrolment endpoints all still respond as before.
- [ ] `GET /api/classes/assigned` returns only classes whose `faculty_id` is the caller, each labelled with its course/semester/department/institute and a student count, and returns an empty list (not an error) for a faculty member with no classes.
- [ ] `POST /api/attendance/recognize` with a photo returns a proposal partitioning the roster into `recognized` / `unrecognized` / `not_enrolled` with no student appearing twice and none missing, and **writes nothing** — verified by the collections being empty afterwards.
- [ ] A student with **zero** face samples always lands in `not_enrolled`, never in `unrecognized`.
- [ ] A face matching nobody on the roster increments `unknown_faces` and is attached to no student.
- [ ] Two detected faces both matching one student mark that student present **once**, with the better distance, and the surplus face counted as unknown.
- [ ] A match just inside the tolerance is reported as `"low"` confidence; a close match is `"high"`.
- [ ] Encodings from students in **another** class cannot produce a match — verified with two classes whose rosters do not overlap.
- [ ] A video capture returns `frames_analyzed > 1`, unions recognitions across frames, and respects the frame cap; a student recognised in only one frame is proposed present.
- [ ] **No media bytes are persisted anywhere** — nothing is written to disk after the request, no media field exists on either stored document, and no log line contains media data. Any temp file used for video decoding is deleted even when the request fails.
- [ ] `POST /api/attendance` creates one session and one record per enrolled student, with `date` stored as a real BSON `date` at UTC midnight (verified in MongoDB — `typeof` is `date`, not `string`) and `taken_by` matching the acting faculty member.
- [ ] Absent students are stored as explicit `"absent"` records, not omitted.
- [ ] A `records` list missing a student, containing a duplicate, or naming a student not on the roster each return `400` and store nothing.
- [ ] A second save for the same class and date returns `409`; the same save with `replace: true` returns `200`, leaves exactly one session, replaces its records, and updates `updated_at`.
- [ ] A future `date`, a malformed `date`, a malformed ObjectId, an invalid `status`, and an invalid `marked_by` each return `400`.
- [ ] `GET /api/attendance/session` returns the saved session with its records for a taken date and `404` for a date with no session.
- [ ] A faculty member acting on a class assigned to **someone else** receives `403` on all three class-scoped endpoints; a class with no assigned faculty is likewise `403`, not `500`.
- [ ] An authenticated **admin** token and an authenticated **student** token each receive `403` on all four endpoints, and an unauthenticated request receives `401`.
- [ ] A multipart `POST /api/attendance/recognize` with valid cookies but no `X-CSRF-TOKEN` header is rejected, confirming CSRF still applies to the upload route.
- [ ] A request with neither an `image` nor a `video` part, one with both, a non-media file, and a corrupt file each return `400` with a clear message — never a `500` or a traceback.
- [ ] Media above the request size limit returns `413` as JSON on the `{"error": ...}` contract, not an HTML page.
- [ ] With the recognition library unavailable the app still starts, `/api/health` returns `200`, the `01`–`06` suites pass, and a recognise request returns `503`; with OpenCV unavailable but `face_recognition` present, a photo still works and only video returns `503`.
- [ ] `/faculty/attendance` lists the faculty member's own classes, takes a date, captures by **file upload** and by **webcam**, shows the three groups plus the unknown-face warning, allows flipping any student, and saves — each step updating without a full page reload.
- [ ] Flipping a recognised student to absent (or vice versa) stores that record with `marked_by: "faculty"`, while untouched proposals store `"recognition"`.
- [ ] The UI surfaces a backend `400`/`403`/`404`/`409`/`413`/`503` as the server's own message, and the `409` specifically offers the replace path rather than a dead end.
- [ ] The webcam stream stops when capture ends or the component unmounts, on both `/faculty/attendance` and `/admin/face-enrollment`, after the `useCamera` extraction.
- [ ] `/admin/face-enrollment` still uploads, captures, lists, and deletes exactly as before the `FaceCapture.jsx` change.
- [ ] No `cv2` import exists outside `backend/recognition/frames.py`, and no `face_recognition` import exists outside `backend/recognition/encoder.py`.
- [ ] `CLAUDE.md` reflects the new Attendance capture status and the deferred items.
