# Spec: Student Attendance Dashboard

## Overview

Attendance has been recorded since `07` and correctable since `08`, but the person it is actually about has never been able to see it. `/student` is still the stub `01` created: a heading, a name, and a logout button. A student today learns their attendance standing by asking a faculty member, which is precisely the manual, lecture-time-consuming loop this project exists to remove — and it means the "early warning" the project promises arrives, if at all, too late to act on.

This feature gives a student a read-only view of their **own** attendance: how they stand in each class they are enrolled in, how they stand overall, how that has moved month by month, and what was recorded for each individual lecture. It is deliberately the *student's own* half of the register. It records nothing, changes nothing, and shows nothing about anybody else — no roster, no class average, no other student's status, no comparison. It also does not judge: there is no pass/fail threshold here, no risk score, and no email. A threshold is configuration that `11` introduces along with the notification that uses it, and risk is `10`'s model. `09` answers "what is my attendance?" and stops there.

It introduces no collection, no field, no index, and no dependency. Every number it reports is derived at read time from `attendance_records` and `attendance_sessions`, and the index that serves it — `idx_student_id_class_id` — was created in `02` with a comment saying it exists for exactly this query.

## Depends on

- `01-project-foundation` — `create_app()`, blueprint registration, the React routing shell, and the `/student` stub this replaces.
- `02-database-setup` — `database/schema.py` as the single declaration point for collection names, and `idx_student_id_class_id` on `attendance_records`.
- `03-authentication` — `role_required(*roles)` (`backend/auth/decorators.py`), HttpOnly-cookie JWT + CSRF, `get_jwt_identity()`, `requestJson` (`frontend/src/api/client.js`), `AuthContext`, `ProtectedRoute`.
- `04-academic-hierarchy-management` — the `classes`/`courses`/`semesters`/`departments`/`institutes` chain whose names label a class on screen.
- `05-admin-user-management` — the `class_enrollments` roster (and its `idx_student_id`), `users/assignments.py`, and the `common/errors.py` + `common/validators.py` + `common/http.py` + `common/serializers.py` shared layer.
- `07-attendance-capture` — the `attendance_sessions` / `attendance_records` collections this reads, `attendance/errors.py` (`ForbiddenError`), `attendance/serializers.py`, `attendance/validators.py`, the `attendance_bp` blueprint and its error handlers, and `frontend/src/api/attendance.js`.
- `08-faculty-attendance-history` — `parse_optional_date` and the inclusive `from`/`to` range rule, which this feature reuses rather than re-deriving.

## APIs

Two endpoints, both added to the existing `attendance_bp` blueprint (`url_prefix="/api"`) — a separate blueprint for the same resource would need its own copy of five error handlers. **Both are `student` only, and both identify the student from the verified JWT.**

- `GET /api/attendance/me` — the caller's attendance in every class they are enrolled in, plus an overall roll-up — **student**
- `GET /api/attendance/me/sessions?class_id=…&from=YYYY-MM-DD&to=YYYY-MM-DD` — the caller's own lecture-by-lecture record for one enrolled class, newest first, with a month-by-month trend. `from`/`to` are optional and inclusive — **student**

`me` is a literal path segment, not a placeholder. **There is no endpoint that takes a student id.** That is the central design decision of this feature: the subject of every query is the token's own identity, so there is no addressable "some other student" to authorize, guess, enumerate, or forget to check. Any future admin- or faculty-facing view of a *named* student's attendance is a different endpoint with a different authorization rule, and it is not this feature.

**Status codes** — the same `{ "error": "..." }` contract as `01`–`08`:

- `200` — successful read
- `400` — missing or malformed `class_id`, malformed `from`/`to`, or a `to` earlier than `from`
- `401` / `403` — unauthenticated / not a student (`role_required`), **or** a student asking about a class they are not enrolled in (`403`)
- `404` — the class does not exist
- `500` — database error, via the blueprint-level `PyMongoError` / `RuntimeError` handlers that already exist

No `409` (nothing is written), no `503` (no CV library is touched on either path).

**Overview response** (`GET /api/attendance/me`):

```json
{
  "classes": [
    {
      "id": "...",
      "name": "A",
      "course": "Data Structures",
      "semester": "Semester 3",
      "department": "Computer Engineering",
      "institute": "...",
      "present_count": 20,
      "absent_count": 4,
      "total_count": 24,
      "percentage": 83.3
    }
  ],
  "overall": {
    "present_count": 51,
    "absent_count": 9,
    "total_count": 60,
    "percentage": 85.0
  }
}
```

**Class detail response** (`GET /api/attendance/me/sessions`):

```json
{
  "class": { "id": "...", "name": "A", "course": "…", "semester": "…", "department": "…", "institute": "…" },
  "present_count": 20,
  "absent_count": 4,
  "total_count": 24,
  "percentage": 83.3,
  "monthly": [
    { "month": "2026-07", "present_count": 8, "absent_count": 1, "total_count": 9, "percentage": 88.9 }
  ],
  "sessions": [
    { "date": "2026-08-14", "status": "present" }
  ]
}
```

Four things about these shapes are deliberate:

- **`total_count` is the number of lectures this student has a record for, not the number the class has held.** Records are written one per student per session, so a student enrolled in week six simply has no row for weeks one to five, and those lectures are not counted against them. Using the class's session count as the denominator would invent absences for someone who was not yet in the class.
- **`percentage` is `null`, never `0`, when `total_count` is `0`.** A class where attendance has not been taken yet is not a class the student has missed everything in, and rendering `0%` would tell them the opposite of the truth.
- **`monthly` is oldest-first and `sessions` is newest-first.** A trend is read left to right; a log is read newest first. The two orderings in one response are intentional and must not be "corrected" into agreement.
- **`monthly` covers the whole class**, not the filtered window, only insofar as `from`/`to` narrows both together — the filter applies to the sessions and the trend identically, so the numbers on the screen always describe the same set of lectures.

**What a student response never carries:** another student's name, id, or status; a roster; a class average or rank; `marked_by`; `taken_by`; `updated_by`; `source`; or a session id. `marked_by` and `source` are audit provenance for judging how well recognition performs — `07` was explicit that recognition proposes and faculty decide, so the stored status is the faculty member's statement, and showing a student "a camera decided this" would misrepresent both what happened and who is answerable for it. A session id is omitted because every session-by-id endpoint is faculty-only, so it is an identifier the student cannot use and should not hold.

## Database changes

**No database changes.** No new collection, no new field, no new index, no migration, no backfill.

The two queries this feature issues are already served:

- "every lecture this student was recorded for, in this class" rides `idx_student_id_class_id` on `attendance_records` — created in `02` with the comment *"For the per-student queries the dashboard, risk model, and low-attendance notifications will each make."* This is that dashboard.
- "which classes am I in" rides `idx_student_id` on `class_enrollments`; resolving those classes and their sessions rides `_id`.

Nothing here writes. Every number in every response is derived at read time. **No percentage, count, or trend is ever stored** — a stored aggregate is a second source of truth that goes stale the first time `08`'s edit or delete changes a record behind it.

## Frontend

- **Create:**
  - `frontend/src/routes/student/AttendanceOverview.jsx` — the `/student/attendance` screen: overall standing, a row per enrolled class, and a detail panel for whichever class is selected showing its trend and its lecture list.
  - `frontend/src/components/student/AttendanceBar.jsx` — a horizontal percentage bar with its figure alongside. Used twice: once for the overall standing and once per class row. One component because they are the same measurement at two scopes, and a second copy would drift the moment the `null` case is handled differently in one of them.
  - `frontend/src/components/student/AttendanceTrend.jsx` — the month-by-month trend, rendered from the `monthly` array.
  - `frontend/src/components/student/LectureStrip.jsx` — one mark per lecture in chronological order, rendered from the `sessions` array.

- **Modify:**
  - `frontend/src/api/attendance.js` — add `getMyAttendance()` and `getMyClassAttendance(classId, { from, to })` in the existing style: build the query string, unwrap the JSON, let `requestJson` throw the backend's own `error` message. Its module docstring currently says "faculty attendance-capture API" and must be widened; a second attendance client file would split one resource's client in two for no boundary.
  - `frontend/src/routes/StudentDashboard.jsx` — replace the stub body with the same link-hub shape `FacultyDashboard.jsx` uses, pointing at `/student/attendance`.
  - `frontend/src/App.jsx` — add `/student/attendance` wrapped in `<ProtectedRoute role="student">`.
  - `frontend/src/index.css` — the classes the four visuals need: bar track and fill, trend column, lecture mark in its filled and hollow states. Only a bar's computed width belongs inline as a style; a colour or a dimension written into JSX is a value nothing else can reuse and nothing can theme.

The student UI must:

- show the overall standing and every enrolled class without the student having to choose anything first;
- order the class rows **weakest percentage first**, with not-yet-taken classes last. The backend returns them in a stable reading order (course, then class name, as `list_assigned_classes` already does); putting the class most in need of attention at the top is a presentation decision and belongs in the screen, not in the query;
- render a class with no attendance taken yet as "not taken yet" rather than as `0%`;
- show an empty-state message, not an error, for a student enrolled in no classes at all;
- open a class to its trend and its lecture list, and let the student narrow that by date range;
- present each lecture as a date and present/absent only — no provenance, no session id, nobody else;
- surface a backend `400`/`403`/`404` as the server's own message;
- update without a full page reload.

### Visualisation

Four visuals, and no more. Every one is built from sized `div`s or inline SVG. **No charting library** — not Recharts, Chart.js, D3, or anything else, and no server-rendered image either. What this screen plots is a handful of rectangles; a dependency added to draw them is a dependency added forever, and `matplotlib` in particular would put presentation in the Flask layer and hand the student a picture they cannot select, theme, or have read aloud.

1. **Overall standing** — one large percentage, one `AttendanceBar`, and the raw figure beneath it (`51 of 60 lectures`). No donut and no gauge: a single proportion does not need a second dimension to be read.
2. **Per-class bars** — an `AttendanceBar` on every class row, weakest first. This is the visual that carries the feature: a column of numbers asks the student to compare them, a column of bars shows them which class is the problem before they have read a single figure.
3. **Monthly trend** — one bar per month in the class detail, oldest to newest, from the `monthly` array. Directional information a single percentage cannot hold: 78% climbing and 78% falling are the same number and not the same situation.
4. **Lecture strip** — one small mark per lecture in the class detail, filled for present and hollow for absent. This shows clustering, which a percentage structurally cannot: four absences in one week and four scattered across a semester produce an identical figure and mean different things. It reads oldest to newest, so the strip and the trend above it run the same direction, while the lecture *list* beneath stays newest-first the way a log is read. Both are the one `sessions` array; the strip reverses it for display and neither ordering is a bug to be reconciled.

Deliberately excluded: a present/absent pie or donut per class (a two-slice pie reads worse than a bar and cannot be compared across classes), a gauge, a calendar heatmap (a month grid is a lot of code for two or three lectures a week), and a cumulative-percentage line (it reads 0% or 100% after the first lecture, which is noise presented as a trend, and it is the only one of these that would genuinely need a plotting library).

**Never encode present/absent by colour alone.** Every bar carries its percentage as text beside it, and the lecture strip distinguishes present from absent by fill as well as by colour, with each mark labelled by date and status for assistive technology. This is the one screen in the project a student is guaranteed to open, and a red/green-only design is unreadable to a colourblind one.

## Backend

- **Create:**
  - `backend/attendance/summary.py` — the student-facing read side: `require_enrolled_class`, `class_attendance_overview`, and `student_class_attendance`. Kept out of `attendance/service.py` for three reasons that all point the same way: the consumer is different (the student, not the class's holder), the authorization rule is different (enrollment, not ownership), and `service.py` imports the recognition package at module level while nothing on this path has any business near the capture machinery. It is also where `10` and `11` will come for the same aggregates.
  - `backend/academic/context.py` — `class_hierarchy_context(db, classes)`, moved verbatim from `attendance/service.py::_hierarchy_context`. Both the faculty class list and the student overview need a class labelled with its course/semester/department/institute names, and the function resolves the academic hierarchy, which is `academic`'s domain rather than attendance's. Moving it is what keeps `summary.py` from importing `service.py` merely to borrow one helper.

- **Modify:**
  - `backend/attendance/service.py` — import `class_hierarchy_context` from `academic/context.py` and delete the local `_hierarchy_context`. Nothing else; `list_assigned_classes` behaves exactly as before.
  - `backend/attendance/validators.py` — extract the `from`/`to` parsing and the `to >= from` check out of `parse_session_filters` into `parse_date_range(args)`, and have both `parse_session_filters` and the new student endpoint call it. One date-range rule, two callers.
  - `backend/attendance/serializers.py` — add `serialize_student_overview` and `serialize_student_class_attendance`, plus a shared `attendance_percentage(present, total)` helper so the round-to-one-decimal and `null`-on-zero rules exist in exactly one place and are used by the per-class, overall, and monthly figures alike. Both new serializers build from literal allow-lists, like everything else in the module.
  - `backend/users/assignments.py` — add `list_student_classes(db, student_id)`: the class documents a student is enrolled in, the inverse of the existing `list_enrollments(db, class_id)`. It belongs here because this module is where "linking people to classes" already lives, and reading that link is the same relationship in the other direction.
  - `backend/routes/attendance.py` — add the two handlers, thin exactly like the ones already there, and widen the module docstring, which currently claims every endpoint in the file is faculty-only. No new error handlers: `ValidationError`, `ForbiddenError`, `NotFoundError`, `PyMongoError`, and `RuntimeError` are all already mapped.
  - `backend/common/serializers.py` — promote the timezone reattachment that `to_json_value` performs inline, and that `attendance/serializers.py` already duplicates as `_as_utc`, into a shared `as_utc(value)`. This feature is the third caller and the first to *compare* a stored date rather than only render one: the range filter puts a naive UTC date from pymongo against a timezone-aware bound from the validators, and mixing those raises `TypeError`. That exact bug shipped once in `08` (`_is_edited`) and was invisible to the whole test suite, because the in-memory fakes store aware datetimes and only a live database surfaces it. One definition is the fix that holds.
  - `CLAUDE.md` — mark the student dashboard implemented in the "Implemented vs stub features" table, and record what this feature defers.

## Files to change

- `backend/attendance/service.py`
- `backend/attendance/serializers.py`
- `backend/attendance/validators.py`
- `backend/routes/attendance.py`
- `backend/users/assignments.py`
- `backend/common/serializers.py`
- `CLAUDE.md`
- `frontend/src/api/attendance.js`
- `frontend/src/routes/StudentDashboard.jsx`
- `frontend/src/App.jsx`
- `frontend/src/index.css`
- `frontend/src/utils/lecture.js` — docstring only. `describeClass` is reused for the student's enrolled classes rather than copied, so the module is no longer faculty-only and should not claim to be.

## Files to create

- `backend/attendance/summary.py`
- `backend/academic/context.py`
- `frontend/src/routes/student/AttendanceOverview.jsx`
- `frontend/src/components/student/AttendanceBar.jsx`
- `frontend/src/components/student/AttendanceTrend.jsx`
- `frontend/src/components/student/LectureStrip.jsx`

## New dependencies

No new dependencies. This feature is two read endpoints and a screen over collections that already exist. `scikit-learn` (`10`) and any SMTP/email library (`11`) must not appear — `backend/tests/test_no_secrets_and_scope.py` already asserts the first of those and must keep passing.

## Rules for implementation

- **The student is always the JWT identity.** `get_jwt_identity()` is the only source of the student id on both endpoints. No student id is ever read from a path segment, query string, or body — not as an override, not as a convenience, not "for admins later". This is what makes cross-student access structurally impossible rather than merely checked.
- **Both endpoints are `role_required("student")`.** Faculty and admin receive `403`, not a fallback view. A faculty member reading one student's attendance is a real future feature with a different ownership rule; it is not this one, and quietly allowing it here would ship an unreviewed authorization path.
- **A class the student is not enrolled in is `403`; a class that does not exist is `404`.** `require_enrolled_class` mirrors `require_owned_class`: resolve the class first, then the relationship, and answer the honest status rather than collapsing both into one. Returning an empty result for an unenrolled class instead of `403` would be worse, not safer — it silently answers a question the caller was not entitled to ask.
- **Never expose another student.** No response on either path may contain another student's id, name, email, status, or any figure derived from one — no roster, no class average, no rank, no "x of y students present". `student_summary` and `serialize_session` are the faculty shapes and must not be reused here; the whole reason these are new serializers is that the faculty shapes carry the class.
- **Never expose attendance provenance to the student.** No `marked_by`, `source`, `taken_by`, `updated_by`, or session id in any student response.
- **The denominator is the student's own records.** Count what this student has rows for, never the class's session count. A student enrolled after a lecture must not be marked down for it, and one unenrolled since must still see the lectures they were present for.
- **`percentage` is derived, rounded to one decimal, and `null` when there is nothing to divide by.** One helper produces it everywhere. Nothing stores it.
- **Count in Python, from one indexed query per collection.** The bound here is one student — a few hundred small rows across every class they take — which is the same reasoning `_roster_counts` in `service.py` already follows. Do not add an aggregation pipeline: `_session_counts` groups in the database because its bound is every record of every session on a page, and that is a different query. If a later feature needs these numbers for a whole class or institute, that is an aggregation and it belongs beside `_session_counts`, not here.
- **Join records to sessions in Python with two indexed queries**, the way `list_enrollments` and `_load_session_records` already do, rather than with a `$lookup`. `attendance_records` does not carry the lecture date; the date lives on the parent session, and every date-shaped answer here (the filter, the trend, the lecture list) depends on that join being correct.
- **One class at a time on the detail endpoint.** `class_id` is required. There is no unpaged "every lecture I have ever attended" feed, because one class for one student is a naturally bounded set and the whole institute is not.
- **Reuse before rewriting.** `parse_object_id`, `parse_optional_date`, the inclusive `from`/`to` rule, `to_json_value`, `class_hierarchy_context`, `ForbiddenError`, the blueprint's error handlers, `requestJson`, `ProtectedRoute`, and `AuthContext` all exist. A second date-range parser, a second hierarchy-name resolver, a second forbidden error, or a second API client for the same resource is a defect.
- **Move `_hierarchy_context`, do not copy it.** After the move there is exactly one definition, `attendance/service.py` calls the moved one, and `list_assigned_classes` still returns what it returned before.
- **Keep business logic out of route handlers.** `routes/attendance.py` translates HTTP ↔ domain; `attendance/summary.py` owns the rules and holds no Flask objects.
- **Import collection names from `database/schema.py`** (`ATTENDANCE_SESSIONS`, `ATTENDANCE_RECORDS`, `CLASS_ENROLLMENTS`, `CLASSES`). No hardcoded collection-name literal anywhere else.
- **No CV import anywhere on these paths.** Nothing here decodes an image or a video. Both endpoints must work on a server with neither `dlib` nor OpenCV built; if either can return `503`, something has been imported that should not have been.
- **Never leak internals.** Driver exceptions are translated into the clean JSON contract and logged server-side.
- **Never hardcode academic data.** Classes, enrollments, and hierarchy names come from MongoDB.
- **The four visuals are hand-built from data the API already returns.** No charting library, no plotting dependency, and no server-rendered chart image: `frontend/package.json` and `backend/requirements.txt` both gain nothing. If a visual appears to need an API field this spec does not already define, the visual is wrong, not the API.
- **No status is conveyed by colour alone**, and a `null` percentage is never drawn as an empty bar — a class with nothing recorded reads as "not taken yet", which is a different statement from 0%.
- **Preserve existing functionality.** `/api/health`, `flask init-db`, `flask create-admin`, every auth endpoint, the twenty academic endpoints, the ten user-management endpoints, the five face-enrolment endpoints, and all eight attendance endpoints must keep working, along with `/admin/academics`, `/admin/users`, `/admin/face-enrollment`, `/faculty/attendance`, and `/faculty/attendance/history`. The `_hierarchy_context` move and the `parse_session_filters` extraction are refactors, not redesigns: `GET /api/classes/assigned` and `GET /api/attendance/sessions` must behave identically afterwards.
- **Do not build what `10` and `11` own:** no attendance threshold, no pass/fail or "low attendance" judgement, no risk score or prediction, no email, no notification record, and no `scikit-learn`. This screen reports a number; deciding what number is too low, and telling anyone about it, is `11`.
- **Deferred deliberately, to be recorded in `CLAUDE.md` rather than solved here:** a student cannot see the threshold they need to clear (it arrives with `11`, and this dashboard should surface it then); faculty and admins still cannot view a named student's attendance; there is no export; there is no per-lecture explanation of *why* a student was marked absent; and there is no notification of any kind.
- **Tests must not use real production credentials, a production database, real personal data, or any real biometric data.** Sessions, records, and enrollments are seeded directly; no capture, no encoder, and no media fixtures are needed anywhere in this feature's tests. The existing `attendance_test_helpers.py` fakes are extended where a new query shape needs them — deliberately and narrowly, as that file's own docstring instructs — rather than duplicated into a second set of fakes.

## Definition of done

- [ ] `python app.py` starts; `/api/health`, the auth endpoints, the twenty academic endpoints, the ten user-management endpoints, the five face-enrolment endpoints, and all eight attendance endpoints still respond as before.
- [ ] `flask init-db` is unchanged and still idempotent — this feature adds no collection, validator, or index.
- [ ] `GET /api/attendance/me` returns one entry per class the caller is enrolled in, each labelled with its course, semester, department, and institute names, plus an `overall` roll-up whose counts equal the sum of the per-class counts.
- [ ] A student enrolled in no classes receives `200` with an empty `classes` list and an `overall` of zeros — not a `404` and not a `500`.
- [ ] A class the student is enrolled in for which no attendance has been taken appears with `total_count: 0` and `percentage: null`, not `percentage: 0`.
- [ ] Attendance recorded for a class the student is **not** enrolled in never appears in either response, and neither does another student's record for a class they share.
- [ ] A student enrolled after some lectures were taken has those lectures excluded from both numerator and denominator: their percentage is computed only over the sessions they have a record for.
- [ ] `GET /api/attendance/me/sessions?class_id=…` returns that student's own status for every lecture they were recorded in, newest first, with `monthly` oldest-first, and `present_count + absent_count == total_count` in the header, in every monthly bucket, and against the length of `sessions`.
- [ ] `from` / `to` filter inclusively on the lecture date and narrow `sessions` and `monthly` to the same set; a `to` earlier than `from`, a malformed date, a missing `class_id`, and a malformed `class_id` each return `400`.
- [ ] No response on either endpoint contains another student's id, name, email, or status, nor `marked_by`, `source`, `taken_by`, `updated_by`, or a session id — asserted against the serialized body, not just read by eye.
- [ ] A student requesting `class_id` for a class they are not enrolled in receives `403`; a non-existent class id receives `404`; neither returns `500`.
- [ ] An authenticated **faculty** token and an authenticated **admin** token each receive `403` on both endpoints, and an unauthenticated request receives `401`.
- [ ] Neither endpoint accepts a student id from the URL, query string, or body: sending one alongside a valid request changes nothing about the response, and no route in the project exposes another student's attendance.
- [ ] With neither `dlib` nor OpenCV installed, both endpoints work normally — neither returns `503`, and no CV module is imported on either code path.
- [ ] `GET /api/classes/assigned` returns exactly what it returned before the `_hierarchy_context` move, and `GET /api/attendance/sessions` exactly what it returned before the `parse_date_range` extraction — verified by the existing `07`/`08` tests still passing unmodified.
- [ ] `attendance/service.py` contains no `_hierarchy_context` definition and `academic/context.py` contains exactly one.
- [ ] `/student` shows the signed-in student a link to their attendance instead of the `01` stub, and `/student/attendance` is unreachable without a student session.
- [ ] `/student/attendance` shows the overall standing and every enrolled class on load, opens a class to its trend and lecture list, filters by date range, and surfaces a backend `400`/`403`/`404` as the server's own message — each step without a full page reload.
- [ ] The overview draws a percentage bar per class, ordered weakest first with not-yet-taken classes last, and the same bar component for the overall standing; every bar shows its percentage as text beside it.
- [ ] A class with `percentage: null` renders as "not taken yet" — not as an empty bar and not as `0%` — in both the class row and the class detail.
- [ ] The class detail draws one trend bar per month, oldest to newest, and one lecture mark per session in chronological order, with present and absent distinguished by fill as well as by colour and each mark labelled with its date and status.
- [ ] Narrowing the date range updates the trend and the lecture strip to the same set of lectures the counts describe.
- [ ] All four visuals render with no charting library and no server-rendered image: `frontend/package.json` gains no dependency, `backend/requirements.txt` gains no dependency, and no plotting library (`matplotlib` included) appears in either.
- [ ] `backend/requirements.txt` gains no dependency, and `test_no_secrets_and_scope.py` still passes.
- [ ] `CLAUDE.md` reflects the new Student dashboard status and this feature's deferred items.
