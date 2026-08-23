# CLAUDE.md

## Project overview

AutoAttend is a smart attendance management system for a college environment, built with React.js, Flask, MongoDB, face recognition, and machine learning.

Manually calling roll and tracking attendance/academic risk wastes lecture time and gives students and faculty no early warning when attendance or performance starts slipping. AutoAttend addresses this by letting faculty mark attendance from a single classroom photo/video via face recognition, giving students self-service visibility into their own attendance, and using a lightweight ML model to flag students who may be at academic risk so they can be notified early.

The system supports three primary roles:

- **Admin** — manages users, the academic structure, and face enrollment.
- **Faculty** — takes attendance for assigned courses/classes and views related academic data.
- **Student** — views their own attendance, trends, and risk indicators.

There is no public registration. Accounts are provisioned by the admin, and login redirects each user to their role-specific portal (Admin Portal / Faculty Dashboard / Student Dashboard).

The project is developed incrementally. Permanent project-wide rules belong in this file, while feature-specific requirements are the responsibility of `.claude/specs/` — each feature's spec there is the source of truth for that feature's detailed requirements.

---

## Academic domain model

Academic data follows a strict top-down hierarchy:

```text id="a1d2om"
Institute → Department → Semester → Course → Class → Student
```

Each level is scoped to its parent (e.g., a class only makes sense within its department/semester), so selections and queries should filter down this hierarchy rather than across it. This hierarchy is database-driven — do not hard-code institutes, departments, semesters, courses, or classes.

---

## Core capabilities

- **Face recognition attendance** — faculty capture a classroom image/video for a selected academic context (institute/department/semester/course/class/date); the recognition pipeline matches faces against registered students to produce present/absent lists, which faculty review before saving.
- **ML academic-risk prediction** — a scikit-learn model uses attendance/performance data to flag students as an early-warning/decision-support signal, not a high-stakes or certain outcome.
- **Low-attendance notifications** — students falling below an attendance threshold are notified by email (SMTP).

---

## Architecture

```text id="f4v2xk"
AutoAttend/
├── Frontend
│   └── React.js
│
├── Backend
│   └── Flask REST API
│
├── Database
│   └── MongoDB
│
├── Face Recognition
│   ├── OpenCV
│   ├── face_recognition
│   └── NumPy
│
├── Machine Learning
│   └── scikit-learn
│
└── Notifications
    └── SMTP / Email
```

**High-level flow:**

```text id="n3t6uk"
React.js
    ↓
Flask REST API
    ↓
MongoDB
    ↓
Face Recognition / ML / Notifications
```

**Where things belong:**

- React UI → frontend components/pages
- API endpoints → Flask backend
- Database operations → backend database/data layer
- Attendance logic → backend business logic
- Face recognition → dedicated CV/recognition logic
- ML prediction → dedicated ML logic
- Notifications → dedicated notification/email logic
- Configuration/secrets → environment variables

Follow the actual project structure once these responsibilities are organized into specific files or directories.

---

## Code style

- Python: PEP 8, `snake_case` for variables and functions
- JavaScript/React: follow existing project naming and component conventions
- React components: use clear, descriptive names
- Flask routes: keep route handlers focused on API concerns
- Business logic: keep substantial logic outside route handlers where appropriate
- Database logic: keep separate from unrelated API logic
- API responses: maintain consistent response structures
- Error handling: return meaningful HTTP status codes and messages
- Avoid unnecessary duplication and overly complex abstractions
- Keep academic data database-driven where required

---

## Tech constraints

- **React.js** — frontend
- **Flask** — backend web framework
- **MongoDB** — application database
- **OpenCV / face_recognition / NumPy** — face-recognition functionality where required
- **scikit-learn** — machine-learning functionality
- **SMTP/email libraries** — notifications
- **No unnecessary dependencies** — introduce new packages only when genuinely required
- **No unnecessary frameworks** — follow the established project stack

Do not replace the project's core technologies with alternatives without an explicit requirement.

Optional technologies (e.g. Gemini API, Cloudinary) may only be introduced when there is a genuinely useful, specific feature that needs them — never added speculatively or just to use generative AI/cloud storage.

---

## Subagent Policy

- Use an explore subagent when codebase exploration is required before implementing a new feature.
- Use `autoattend-test-writer` when a feature needs tests.
- Use `autoattend-test-runner` after tests have been written.
- Use `autoattend-quality-reviewer` and `autoattend-security-reviewer` for feature code review.
- Quality and security reviewers should run in parallel.
- Do not skip the feature specification when the development workflow requires one.
- Do not make code changes based on review findings until explicitly approved when using `/code-review-feature`.

Available project agents:

- `autoattend-quality-reviewer`
- `autoattend-security-reviewer`
- `autoattend-test-writer`
- `autoattend-test-runner`

---

## Commands

Backend code lives in `backend/`, frontend code lives in `frontend/`. The Python virtual environment (`venv/`) lives at the repo root.

```bash id="b7f5em"
# Backend environment (run from repo root)
python -m venv venv

# Windows
venv\Scripts\activate

# Linux / macOS
source venv/bin/activate

# Install backend dependencies
pip install -r backend/requirements.txt

# face_recognition depends on dlib, which compiles from source. On Windows
# install CMake and the Visual Studio C++ Build Tools first, then re-run the
# install above. Everything except face enrollment works without it: the
# recognition library is imported lazily, so the API still starts and the
# test suite still passes, and only the face-enrollment endpoints report 503
# until dlib is built.

# Run Flask backend
cd backend
python app.py

# Create the first admin user (no public registration exists)
flask create-admin

# Run all tests
pytest

# Run a specific test file
pytest tests/test_foo.py

# Run a specific test
pytest -k "test_name"

# Run tests with output visible
pytest -s
```

```bash id="v3c8fr"
# Frontend (Vite + React, run from frontend/)
cd frontend

# Install frontend dependencies
npm install

# Run frontend dev server
npm run dev

# Build for production
npm run build
```

---

## Feature Development Workflow

Use the following workflow for major features:

```text id="m6q2bd"
/create-spec
      ↓
Implement feature
      ↓
/test-feature
      ↓
/code-review-feature
      ↓
Fix approved issues
      ↓
Commit
```

Feature-specific specifications belong in:

```text id="x9h8la"
.claude/specs/
```

Each feature specification is the source of truth for that feature.

The active feature specification must be checked before implementation, testing, and code review.

---

## Implemented vs stub features

Keep track of incomplete or intentionally stubbed features as the project develops.

| Feature | Status |
|---|---|
| Authentication | Implemented — Flask-JWT-Extended login/refresh/logout/me via HttpOnly cookies, role-based `@role_required`, `flask create-admin` bootstrap. No public registration. |
| Academic hierarchy | Implemented — CRUD REST APIs for all five levels (`/api/institutes`, `/api/departments`, `/api/semesters`, `/api/courses`, `/api/classes`); writes are admin-only, reads are admin + faculty. Parents are verified on create and deletes are blocked (never cascaded) while children exist. Admin UI at `/admin/academics`. Faculty assignment and student enrollment are now handled by Admin user management (below). |
| Admin user management | Implemented — admin-only `/api/users` CRUD (create/list/get/update, `PUT .../status`, `PUT .../password`), plus `PUT /api/classes/<id>/faculty` and `/api/classes/<id>/students` for enrollment. Accounts are deactivated, never deleted; `role` is immutable after creation; an admin cannot deactivate themselves or the last active admin. Admin UI at `/admin/users`, with the class assignment panel inside `/admin/academics`. Deferred: pagination on the user list, and invalidating an already-issued access token on password reset/deactivation (bounded by the 15-minute access-token lifetime). |
| Face enrollment | Implemented — admin-only `/api/students/<id>/face-encodings` (list/register/delete one/delete all) plus `GET /api/classes/<id>/face-enrollment` for per-student sample counts across a roster. Stores only the 128-value encoding in the `face_encodings` collection; the source image is processed in memory and never persisted, and the encoding vector never appears in a response. All CV code is confined to `backend/recognition/encoder.py`, which imports `face_recognition`/`numpy` lazily — where `dlib` is not built, the app still starts and these endpoints return `503`. Admin UI at `/admin/face-enrollment` (file upload + webcam capture). Deferred: students/faculty cannot see enrollment status, there is no bulk import, and changing the detector model has no re-encoding migration path (the stored `model` field is what makes one possible later). |
| Admin portal | Follow current implementation status |
| Faculty portal | Partial — the dashboard links to attendance capture and attendance history (below). Nothing else on it is built yet. |
| Student dashboard | Implemented — student-only `GET /api/attendance/me` (per-class + overall standing for every enrolled class) and `GET /api/attendance/me/sessions?class_id=` (that student's own status per lecture, plus a month-by-month trend; optional inclusive `from`/`to`). Both identify the student from the JWT — **no endpoint anywhere takes a student id**, so another student's attendance is unreachable rather than merely forbidden. An unenrolled class is `403`, a missing one `404`. The denominator is the student's own records, never the class's session count, so a student enrolled late is not marked down for lectures held before them; `percentage` is `null` (not `0`) when nothing has been recorded. Responses deliberately omit `marked_by`, `source`, `taken_by`, `updated_by`, session ids, and anything about another student. Reads only — nothing is written and no count or percentage is stored. Student UI at `/student/attendance`, with four hand-built visuals (overall bar, per-class bars weakest-first, monthly trend, lecture strip) and no charting library. Deferred: students cannot see the attendance threshold they need to clear (arrives with notifications); faculty/admins still cannot view a named student's attendance; no export; no per-lecture explanation of why a student was marked absent. |
| Face recognition (attendance matching) | Implemented — `recognition/matcher.py` matches faces detected in a capture against the roster's encodings only, collapsing duplicate claims to the best distance and counting everything else as an unknown face. `recognition/frames.py` is the only `cv2` importer and samples up to 8 frames from a video; both CV libraries stay lazily imported, so a server missing either still starts and only the affected path returns `503`. |
| Attendance system | Implemented — faculty-only `/api/classes/assigned`, `POST /api/attendance/recognize` (proposes, writes nothing), `GET /api/attendance/session`, and `POST /api/attendance` (saves a reviewed list). Every class-scoped endpoint additionally requires `classes.faculty_id` to be the caller (`403` otherwise, including for an unassigned class). Stores `attendance_sessions` (one per class per date, unique index, `date` at UTC midnight) and `attendance_records` (one per student, absences explicit, `marked_by` recording whether recognition or a human decided). The captured photo/video is never persisted — the video temp file OpenCV needs is deleted in a `finally`. Faculty UI at `/faculty/attendance`. Deferred: admins cannot take attendance; no live/streaming recognition; and recognition runs synchronously in the request, so a large video occupies a worker. |
| Faculty attendance history | Implemented — faculty-only `GET /api/attendance/sessions` (one owned class at a time, optional inclusive `from`/`to`, `limit` default 50 / max 200, newest first, counts from one `$group` aggregation and no `records` array), plus `GET`/`PUT`/`DELETE /api/attendance/sessions/<id>`. Ownership is reached through the session, so an unknown id is `404` and another faculty member's session is `403`. An edit changes statuses only (`class_id`, `date`, `source`, `created_at`, `taken_by` immutable) and is validated against the students already in the session rather than today's roster; it stamps the optional `attendance_sessions.updated_by`, while `edited` is derived from `updated_at > created_at`. A delete removes the records first, then the session, and is not recoverable. No CV import on any of these paths. Faculty UI at `/faculty/attendance/history`. Deferred: admins cannot view or edit attendance; no cross-class or institute-wide view; no CSV/PDF export; and there is no per-edit audit trail beyond who last changed it and when. |
| ML prediction | Follow current implementation status |
| Notifications | Follow current implementation status |

**Do not implement an unfinished feature unless the active task explicitly targets it.**

Update this section when major features become implemented.

---

## Warnings and things to avoid

- **Never hardcode secrets** such as MongoDB credentials, JWT secrets, SMTP passwords, API keys, or Cloudinary credentials.
- **Never add public registration/sign-up** — accounts are provisioned by the admin only.
- **Never rely only on frontend authorization** — role permissions must be enforced by the Flask backend.
- **Never expose sensitive biometric data unnecessarily.**
- **Never hardcode academic data** when it should come from MongoDB.
- **Never present ML risk predictions as certain or high-stakes outcomes** — treat them as an early-warning/decision-support signal, and evaluate the model before trusting it.
- **Never put large amounts of business logic directly inside Flask routes.**
- **Never unnecessarily mix face-recognition or ML processing with unrelated application logic.**
- **Never install unnecessary packages mid-feature.**
- **Never treat face recognition as perfectly accurate.** Handle unknown faces, duplicate detections, weak matches, false positives, and false negatives.
- **Never modify attendance without proper authorization and validation.**
- **Never use real production credentials or unnecessary biometric data in tests.**
- **Never implement requirements that contradict the active feature specification.**
- **Never make large unrelated changes while implementing a feature.**
- **Always inspect the existing code before creating new files or duplicating functionality.**
- **Always keep frontend, backend, database, AI/ML, and notification responsibilities reasonably separated.**