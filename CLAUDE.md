# CLAUDE.md

## Project overview

AutoAttend is an automatic attendance management system for a college environment, built with React.js, Flask, MongoDB, and face recognition.

Manually calling roll wastes lecture time, and a student whose attendance is slipping usually finds out too late to fix it. AutoAttend addresses this by letting faculty mark attendance from a single classroom photo/video via face recognition, giving students self-service visibility into their own attendance, and emailing students whose attendance falls below the required threshold.

**The project is built around attendance and nothing else.** It does not record marks, grades, or assessments, and it does not predict academic risk. An earlier draft of this file described a scikit-learn academic-risk model; that was never built and has been cancelled deliberately — see the "Warnings" section. The machine-learning component of this project is the face recognition itself.

The system supports three primary roles:

- **Admin** — manages users, the academic structure, and face enrollment.
- **Faculty** — takes attendance for assigned courses/classes and views related academic data.
- **Student** — views their own attendance and trends.

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
- **Low-attendance notifications** — students falling below an attendance threshold are notified by email (SMTP). The threshold is a plain configured percentage, checked against recorded attendance; there is no model and no prediction involved.

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
Face Recognition / Notifications
```

**Where things belong:**

- React UI → frontend components/pages
- API endpoints → Flask backend
- Database operations → backend database/data layer
- Attendance logic → backend business logic
- Face recognition → dedicated CV/recognition logic
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

### Stylesheets

`frontend/src/index.css` is an `@import` manifest and holds no rules. The rules live in `frontend/src/styles/`, and the manifest's order **is** the cascade — Vite inlines the files into one stylesheet at build, so this is still a single global namespace with no CSS modules.

```text id="c5s9ty"
frontend/src/index.css        the @import manifest -- order IS the cascade
frontend/src/styles/
  tokens.css                  the palette, light and dark, + the radii
  base.css                    element defaults, .visually-hidden, focus
  components.css              .card .btn .callout .pill .form-field
  login.css                   .auth-*
  shell.css                   .app-*, .page*, .portal-*  (temporary)
  student-dashboard.css       .student-home-*
  student-attendance.css      .sa-*, .attendance-bar*, .trend*, .lecture-*
  faculty-dashboard.css       .faculty-home-*
  faculty-roster-row.css      .fa-row*, .fa-flag*, .fa-toggle*  (one
                              component, rendered by BOTH faculty
                              attendance pages -- see below)
  faculty-attendance.css      .fa-*  (capture)
  faculty-history.css         .fh-*  (history)
  admin-portal.css            .admin-portal-*
  admin-academics.css         .ah-*  (hierarchy + class assignment)
  scaffolding.css             every page not yet redesigned
```

- **`scaffolding.css` is temporary and should only ever shrink.** It holds the styling written while the features were being built. Its class names do not partition by page — `.hierarchy-*` alone still reaches seven files across admin — which is why it is one file rather than several. It was 396 lines when the redesign began and is 210 now. **It did not shrink during group 3**: every class the faculty pages borrowed also had an admin borrower, so those pages stopped *using* them without being able to delete them. Group 4a took the first 13 lines off it (`.hierarchy-select` and `.is-selected`, both exclusive to `HierarchyLevel.jsx`); group 4b is what empties the rest. Each `/frontend-maker` group carves its pages out of it into `styles/<page>.css`. When it is empty, the redesign is finished.
- **A new page file goes into the manifest before `scaffolding.css`**, so a leftover scaffolding rule cannot win over a finished design.
- **`components.css` is the shared vocabulary — compose it, never restyle it.** Five primitives (`.card`, `.btn` + variants, `.callout`, `.pill`, `.form-field`), each extracted from two or more *designed* pages that had each built the same thing. `.btn--danger` and `.callout--success` were added the same way at the end of group 3, once the capture and history screens had independently written identical rules; both pages had shipped them page-scoped first. It is `.form-field` and not `.field` because `.field` is still owned by `scaffolding.css` and rendered by ten un-redesigned files — a bare `.field` would have restyled every form on those pages. A page composes them and keeps its own hook alongside for what is page-specific (`className="btn btn--secondary sa-toggle"`); anything layout, data-driven or page-only stays on that hook. `.card { padding: 16px }` in a page file is the failure mode — it silently redesigns every other page — so override on the page's own selector instead, which the manifest order already makes work. A sixth primitive or a new variant is added only when two real implementations exist, never invented for one page. This is the opposite of the `scaffolding.css` rule below, and the difference is intent: those classes were never shared on purpose, these are.
- **A component rendered by two pages gets its own stylesheet, not a home in either page's file.** `faculty-roster-row.css` holds `StudentStatusRow`, which both faculty attendance pages render. It is neither a page nor a primitive: keeping it in `faculty-attendance.css` meant the history page's rows were styled by a file named after a different page, and `components.css` is for patterns with two *independent* implementations, whereas this is one component used twice. The split is strict — the file owns what belongs to the row (typography, metadata line, flags, toggle), and each page owns how it *arranges* a list of them (`.fa-roster`, `.fh-roster`). Load it before both page files so either can override with a plain later selector.
- **A redesigned page stops *borrowing* the shared classes rather than restyling them.** `.hierarchy-*`, `.user-*` and `.field` are not a vocabulary — they are whatever each page happened to reach for. Restyling one to suit the page in hand silently redesigns admin and faculty screens nobody has looked at. Rename the page's usages onto its own hooks instead (`.student-home-*`, `.sa-*`), leave the shared rules untouched, and let them die when the last borrower is redesigned.
- **Only `tokens.css` and `base.css` are read by everyone.** Do not add to either to solve one page's problem.
- **Never write a raw colour outside `tokens.css`** — it cannot follow the dark palette, and nothing catches it until someone switches theme.

---

## Tech constraints

- **React.js** — frontend
- **Flask** — backend web framework
- **MongoDB** — application database
- **OpenCV / face_recognition / NumPy** — face-recognition functionality where required
- **SMTP/email libraries** — notifications (Python's stdlib `smtplib`/`email` unless something is genuinely missing)
- **No scikit-learn, and no ML framework of any kind** — the project does no model training or prediction
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
- Use `/frontend-maker <page>` for UI work on an existing page. It runs `autoattend-css-designer` first, stops for approval, then runs `autoattend-responsive-designer`, then a guidelines audit. Do not invoke the two design agents by hand for a page the command already covers.
- `autoattend-responsive-designer` can also be invoked directly for a general responsiveness sweep; it then applies its own "significant UI changes" gate and stops if nothing warrants a review. That gate is skipped when a specific page is named. (It replaced the `/make-responsive` command, which was removed so the rule lives with the agent that applies it.)

Available project agents:

- `autoattend-quality-reviewer`
- `autoattend-security-reviewer`
- `autoattend-test-writer`
- `autoattend-test-runner`
- `autoattend-css-designer`
- `autoattend-responsive-designer`

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

# Email students below the attendance threshold. --dry-run lists who would
# be mailed without opening an SMTP connection or recording anything, and
# works even where the SMTP_* variables are not configured yet.
flask notify-low-attendance --dry-run
flask notify-low-attendance

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
| Student dashboard | Implemented — student-only `GET /api/attendance/me` (per-class + overall standing for every enrolled class, plus the configured attendance `threshold` and a per-class `meets_threshold`/`lectures_to_reach`) and `GET /api/attendance/me/sessions?class_id=` (that student's own status per lecture, plus a month-by-month trend, the same three threshold fields, and optional inclusive `from`/`to`). Both identify the student from the JWT — **no endpoint anywhere takes a student id**, so another student's attendance is unreachable rather than merely forbidden. An unenrolled class is `403`, a missing one `404`. The denominator is the student's own records, never the class's session count, so a student enrolled late is not marked down for lectures held before them; `percentage` is `null` (not `0`) when nothing has been recorded, and `meets_threshold`/`lectures_to_reach` follow it to `null` rather than `false`/`0`. The bar is applied per class only — `overall` and every `monthly` bucket carry none of the three threshold fields — and a misconfigured `LOW_ATTENDANCE_THRESHOLD` degrades the read path to `threshold: null` rather than failing the request. Responses deliberately omit `marked_by`, `source`, `taken_by`, `updated_by`, session ids, and anything about another student. Reads only — nothing is written and no count, percentage, or comparison is stored. Student UI at `/student/attendance`, with four hand-built visuals (overall bar, per-class bars weakest-first, monthly trend, lecture strip) and no charting library; each per-class bar additionally carries a marker at the required percentage and a `ThresholdNote` stating met/below in words, plus a catch-up figure when below. Deferred: faculty/admins still cannot view a named student's attendance; no export; no per-lecture explanation of why a student was marked absent. |
| Face recognition (attendance matching) | Implemented — `recognition/matcher.py` matches faces detected in a capture against the roster's encodings only, collapsing duplicate claims to the best distance and counting everything else as an unknown face. `recognition/frames.py` is the only `cv2` importer and samples up to 8 frames from a video; both CV libraries stay lazily imported, so a server missing either still starts and only the affected path returns `503`. |
| Attendance system | Implemented — faculty-only `/api/classes/assigned`, `POST /api/attendance/recognize` (proposes, writes nothing), `GET /api/attendance/session`, and `POST /api/attendance` (saves a reviewed list). Every class-scoped endpoint additionally requires `classes.faculty_id` to be the caller (`403` otherwise, including for an unassigned class). Stores `attendance_sessions` (one per class per date, unique index, `date` at UTC midnight) and `attendance_records` (one per student, absences explicit, `marked_by` recording whether recognition or a human decided). The captured photo/video is never persisted — the video temp file OpenCV needs is deleted in a `finally`. Faculty UI at `/faculty/attendance`. Deferred: admins cannot take attendance; no live/streaming recognition; and recognition runs synchronously in the request, so a large video occupies a worker. |
| Faculty attendance history | Implemented — faculty-only `GET /api/attendance/sessions` (one owned class at a time, optional inclusive `from`/`to`, `limit` default 50 / max 200, newest first, counts from one `$group` aggregation and no `records` array), plus `GET`/`PUT`/`DELETE /api/attendance/sessions/<id>`. Ownership is reached through the session, so an unknown id is `404` and another faculty member's session is `403`. An edit changes statuses only (`class_id`, `date`, `source`, `created_at`, `taken_by` immutable) and is validated against the students already in the session rather than today's roster; it stamps the optional `attendance_sessions.updated_by`, while `edited` is derived from `updated_at > created_at`. A delete removes the records first, then the session, and is not recoverable. No CV import on any of these paths. Faculty UI at `/faculty/attendance/history`. Deferred: admins cannot view or edit attendance; no cross-class or institute-wide view; no CSV/PDF export; and there is no per-edit audit trail beyond who last changed it and when. |
| ML prediction | **Cancelled — not part of this project.** No model, no training, no `scikit-learn`, and no marks/grades/assessment data to train on. Do not propose or build it. |
| Notifications | Implemented — `flask notify-low-attendance` emails students whose recorded attendance is below `LOW_ATTENDANCE_THRESHOLD` (default 75) in a class they are still enrolled in. **CLI-only by design: no route, no blueprint, no React screen**, so nothing on a request path can send mail; `test_app_factory.py` still asserts no route contains `notification`. The bar is applied **per class**, never to an overall average, and to the same rounded figure `GET /api/attendance/me` shows — a class under `MIN_RECORDED_LECTURES` (5, a constant in `notifications/service.py`) is skipped so nobody is mailed "0%" after one lecture. One email per student lists every class they are short in; it is plain text with no HTML part and no link, carries only that student's own figures, and states a recorded percentage against a configured bar — never a consequence, a prediction, or a risk score. Sends are recorded one row per class in `attendance_notifications`, which is what `NOTIFICATION_COOLDOWN_DAYS` (default 7) reads to avoid mailing the same student about the same class twice; the index is deliberately non-unique so a still-short student can be warned again later. **Send first, then record** — a failed send writes nothing and is retried next run, and one bad address never stops the sweep. `--dry-run` lists recipients while opening no SMTP connection and writing nothing. No new dependency: stdlib `smtplib`/`email`, confined to `notifications/mailer.py`. Deferred: no admin trigger or admin-set threshold (an optional later spec), the bar is global rather than per-institute or per-course, there is no HTML mail, no unsubscribe, no per-run audit beyond the sent rows, and the sweep runs synchronously in one process. |
| App shell | Implemented — `AppShell.jsx` is the root of all nine signed-in pages, providing the skip link, the `<header>` (brand, role nav, user name, logout), the `<main id="main">` landmark, and the page's single `<h1>` from its `title` prop. `NavBar.jsx` marks the current route via `NavLink` (which sets `aria-current="page"` itself), with `end` on every link so the two nested faculty routes cannot both claim to be current. `src/navigation.js` is the **only** place any role's link list is written — the header nav and the three landing pages both read it, so they cannot disagree. Role filtering there decides what is *shown*, never what is allowed: `ProtectedRoute` and `@role_required` are untouched. Removed nine copies of `<h1>`/`Welcome, …`/logout and six hand-rolled back links. `Login.jsx` and `NotFound.jsx` are reachable while signed out, render no shell, and were not touched. **The CSS is deliberately temporary** — it lives in `styles/shell.css`, is marked as such there, and is expected to be replaced by the per-page design groups. The component vocabulary it deliberately did **not** define was extracted later, once two groups had shown what repeats — see `styles/components.css` and the "Stylesheets" section above. Still deferred on purpose: no `.table` or `.modal` primitive (neither has two designed implementations yet), no spacing scale, no responsive pass, no collapsible nav, no manual theme toggle, no breadcrumbs, tabs, or global search. |

**Do not implement an unfinished feature unless the active task explicitly targets it.**

Update this section when major features become implemented.

### Next planned feature

Specs `01`–`13` are built. Every core capability this file describes exists, `12-app-shell` added the frame the UI work sits inside, and `13-component-vocabulary` added the primitives the remaining pages are built from.

**The current styling is scaffolding.** It is being replaced page by page through `/frontend-maker`, in small related groups, in this order:

1. ~~**Login** — `/login`~~ — **done.** `styles/login.css`.
2. ~~**Student** — `/student` + `/student/attendance`~~ — **done.** `styles/student-dashboard.css`, `styles/student-attendance.css`.
3. ~~**Faculty** — `/faculty` + `/faculty/attendance` + `/faculty/attendance/history`~~ — **done.** `styles/faculty-dashboard.css`, `styles/faculty-roster-row.css`, `styles/faculty-attendance.css`, `styles/faculty-history.css`.
4. **Admin** — `/admin` + `/admin/academics` + `/admin/users` + `/admin/face-enrollment`.

   **Run across two sessions** (agreed 2026-08-26), unlike groups 1–3 which took one each. The one-session-per-group convention was set when a group meant two or three pages; this one is four pages and 1,714 lines of JSX across nine files, against faculty's three pages and 1,078 across five.

   - ~~**4a** — `/admin` + `/admin/academics`~~ — **done** 2026-08-27. `styles/admin-portal.css`, `styles/admin-academics.css`.
   - **4b** — `/admin/users` + `/admin/face-enrollment`, then `ConfirmDialog` and the `scaffolding.css` teardown. ← next

One group at a time, each finished before the next starts. `/frontend-maker` takes one page per invocation, so a group is run page by page.

**Group 4 is the last one, and it is the only group that deletes rather than renames.** Every one of `scaffolding.css`'s remaining 210 lines is held alive by an admin borrower — `.hierarchy-*` across seven files, `.field`, `.user-*`, `.face-capture-live`, `.danger`, and the `.dialog*` rules behind `components/admin/ConfirmDialog.jsx`, which both faculty attendance pages render and deliberately left as scaffolding. As those pages move onto their own hooks the file empties, and when it is empty the redesign is finished. 4a proved the mechanism twice: `/admin` deleted `.portal-*` outright from `shell.css` (211 → 183 lines, its last renderer gone), and `/admin/academics` took `.hierarchy-select` and `.is-selected` off `scaffolding.css`. Everything else stays until its last borrower moves in 4b.

**Three decisions 4b has to settle**, all raised by 4a and deliberately left open:

- **The neutral `.pill`.** `/admin/academics` wrote `.ah-flag` page-scoped for deactivated-account markers. `UserManagement` renders the same marker and is the second implementation the extraction rule waits for. Weigh it against whether that page also wants *role* pills — if so the variant has two jobs and should be sized for both.
- **`(pointer: coarse)` as a project pattern.** `admin-academics.css` uses it to restore a 44px tap target on its small action buttons; every other rule in every one of these stylesheets is width-keyed. `/admin/users` has the same buttons, so decide before it repeats the choice.
- **The deferred form-hygiene batch** from the `/admin/academics` audit — `autocomplete`/`name`, `spellCheck={false}` on code and email fields, placeholder ellipsis, focus-first-error. It recurs identically on both 4b pages, so it is one batch across all three rather than three passes.

**After the redesign: a polish pass.** Agreed 2026-08-26 — small improvements to the site once all four groups are done, not yet scoped. Its natural inputs are the `web-design-guidelines` findings that each group reported and left unapplied (the substantive one being that no async confirmation or progress state on either faculty attendance page has an `aria-live` region, so a screen-reader user is never told attendance saved), plus the UI items the shell deferred on purpose — a collapsible nav for the still-three-row mobile header, a spacing scale, a manual theme toggle. Group 4a added two more: the **`.card--link` extraction** described below, and the `Loading…` states on `/admin/academics`, which sit outside a live region even though that page's errors correctly do not — the same `aria-live` gap as the faculty pages, so one pattern should settle all of them. Do not start it before group 4, and do not assume its contents: ask.

**The vocabulary is working as intended.** `13-component-vocabulary` landed between groups 2 and 3, and group 3 composed it rather than adding a fourth copy of anything. It also fed it: the capture and history screens each wrote a destructive button and a success callout page-scoped, and once both existed they were extracted into `.btn--danger` and `.callout--success`. That is the loop — a page wanting something new writes it page-scoped and reports it; the extraction happens when a second, independent implementation appears. Group 4 is doing the same: `/admin/academics` wrote `.ah-flag` page-scoped and reported it, and it waits on `UserManagement` for its second implementation.

**`.card--link` now has all three implementations, and is deliberately deferred to the polish pass.** `/student`, `/faculty` and `/admin` each render a card whose whole area is one nav target, identical across ~90 lines — roughly 180 lines removable. It is not being extracted during group 4, because **the set is closed**: no remaining page is a landing page, so extracting now rather than later changes nothing about the work left, and duplication that cannot grow and is provably in sync is the cheapest kind to leave alone. Extraction also is not mechanical — `/student` would have to move onto the other two's gutter (56/24 → 48/20) and breakpoint (480 → 420), a real if small change to a shipped page. Still open when it is picked up: whether it belongs in `components.css` (it would be the first primitive built from a *coordinated set* of classes joined by `:has()` rather than one class on one control) or in a `styles/portal-card.css` on the `faculty-roster-row.css` precedent.

Beyond that, what remains is only the deferred items already recorded per feature above, none of them scheduled:

- An admin trigger with an admin-set threshold for notifications — the alternative `11` considered and did not choose, since it needs a new blueprint, an admin route, and the threshold moving to MongoDB, and it would undo the deliberately-kept `"notification"` route guard.
- A faculty/admin view of a *named* student's attendance — deliberately ruled out as a "small" feature: `09` built things so that **no endpoint anywhere takes a student id**, and undoing that is a security decision, not a finishing touch.
- CSV/PDF export, user-list pagination, bulk face-enrollment import, and live/streaming recognition.

---

## Warnings and things to avoid

- **Never hardcode secrets** such as MongoDB credentials, JWT secrets, SMTP passwords, API keys, or Cloudinary credentials.
- **Never add public registration/sign-up** — accounts are provisioned by the admin only.
- **Never rely only on frontend authorization** — role permissions must be enforced by the Flask backend.
- **Never expose sensitive biometric data unnecessarily.**
- **Never hardcode academic data** when it should come from MongoDB.
- **Never add marks, grades, assessments, or academic-risk prediction.** AutoAttend is an automatic attendance system; attendance is the only student data it records. A low-attendance email is a threshold check against recorded attendance, never a model output — do not introduce `scikit-learn` or any training/prediction step to produce it.
- **Never put large amounts of business logic directly inside Flask routes.**
- **Never unnecessarily mix face-recognition processing with unrelated application logic.**
- **Never install unnecessary packages mid-feature.**
- **Never treat face recognition as perfectly accurate.** Handle unknown faces, duplicate detections, weak matches, false positives, and false negatives.
- **Never modify attendance without proper authorization and validation.**
- **Never use real production credentials or unnecessary biometric data in tests.**
- **Never implement requirements that contradict the active feature specification.**
- **Never make large unrelated changes while implementing a feature.**
- **Always inspect the existing code before creating new files or duplicating functionality.**
- **Always keep frontend, backend, database, AI/ML, and notification responsibilities reasonably separated.**