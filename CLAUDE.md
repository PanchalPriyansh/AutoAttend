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
                              and the spacing scale
  base.css                    element defaults, .visually-hidden, focus
  components.css              .card .btn .callout .pill .form-field
  confirm-dialog.css          .dialog*  (one component, rendered by
                              SIX files across admin and faculty)
  portal-card.css             .portal-card*  (one component, rendered by
                              ALL THREE landing pages)
  login.css                   .auth-*
  shell.css                   .app-*, .page*  (temporary)
  student-dashboard.css       .student-home  (the grid only)
  student-attendance.css      .sa-*, .attendance-bar*, .trend*, .lecture-*
  faculty-dashboard.css       .faculty-home  (the grid only)
  faculty-roster-row.css      .fa-row*, .fa-flag*, .fa-toggle*  (one
                              component, rendered by BOTH faculty
                              attendance pages -- see below)
  faculty-attendance.css      .fa-*  (capture)
  faculty-history.css         .fh-*  (history)
  admin-portal.css            .admin-portal  (the grid only)
  admin-academics.css         .ah-*  (hierarchy + class assignment)
  admin-users.css             .au-*
  admin-face-enrollment.css   .fe-*  (page + FaceCapture)
```

`confirm-dialog.css` is loaded directly after `components.css`, before
every page; `faculty-roster-row.css` sits immediately before the two
faculty pages that render it. See the manifest's own comment for why
the two shared components sit at different points.

- **`scaffolding.css` is gone, and the redesign is finished.** It held the styling written while the features were being built — 396 lines at the start, 210 after group 4a, 90 after `/admin/face-enrollment`, and deleted outright by the teardown that ended group 4b. Its class names never partitioned by page (`.hierarchy-*` alone reached eleven files), which is why it was one file rather than several. **Every page is now styled by a file named after what it styles.** Do not recreate it: a new page gets its own `styles/<page>.css`, and a shared control is either a `components.css` primitive or its own component file.
- **`components.css` is the shared vocabulary — compose it, never restyle it.** Five primitives (`.card`, `.btn` + variants, `.callout`, `.pill`, `.form-field`), each extracted from two or more *designed* pages that had each built the same thing. `.btn--danger` and `.callout--success` were added the same way at the end of group 3, once the capture and history screens had independently written identical rules; both pages had shipped them page-scoped first. It is `.form-field` and not `.field` because `.field` was owned by `scaffolding.css` and rendered by ten un-redesigned files when it was extracted — a bare `.field` would have restyled every form on those pages. Both are gone now; the name stays, because eleven files render it and renaming would be churn. `.pill--neutral` was added by the group 4b teardown from **three** identical implementations (`.ah-flag`, `.au-flag`, `.fe-flag`), each written page-scoped and reported as evidence first — the extraction waited until all three existed so they could migrate in one edit rather than leaving one page hand-rolling what the page beside it composed. A page composes them and keeps its own hook alongside for what is page-specific (`className="btn btn--secondary sa-toggle"`); anything layout, data-driven or page-only stays on that hook. `.card { padding: 16px }` in a page file is the failure mode — it silently redesigns every other page — so override on the page's own selector instead, which the manifest order already makes work. A new primitive or variant is added only when two real implementations already exist, never invented for one page. This is the opposite of the borrowed-class rule below, and the difference is intent: those classes were never shared on purpose, these are.
- **A component rendered by more than one page gets its own stylesheet, not a home in any page's file.** There are three: `faculty-roster-row.css` holds `StudentStatusRow`, which both faculty attendance pages render; `confirm-dialog.css` holds `ConfirmDialog`, which six files across admin and faculty render; and `portal-card.css` holds `PortalCard`, which all three landing pages render. None is a page or a primitive: keeping the row in `faculty-attendance.css` meant the history page's rows were styled by a file named after a different page, and `components.css` is for patterns with two *independent* implementations, whereas these are single components used several times — which is also why the vocabulary still has no `.modal`. The split is strict: the component file owns what belongs to the component, and each page owns how it *arranges* them (`.fa-roster`, `.fh-roster`, and the three portal grids). Load a component file before every page that renders it, so any of them can override with a plain later selector. **`PortalCard` is the one that was extracted late rather than early**, and it is the cautionary case: three pages each built the same control on their own hooks, each wrote in its own comments that all three should look identical, and by extraction time they had drifted on four values — a gutter, a chevron offset, a breakpoint and the description's type — including a raw `12px` that no longer read as the `--radius-md` it was. Three copies is where a fourth comes from.
- **The rule that emptied `scaffolding.css`, kept because it generalises.** A page being redesigned stopped *borrowing* shared classes rather than restyling them: `.hierarchy-*`, `.user-*` and `.field` were never a vocabulary, just whatever each page had reached for, so restyling one would silently have redesigned screens nobody had looked at. Each page renamed its usages onto its own hooks and left the shared rules untouched; a rule was deleted only once its last renderer was gone. Apply the same discipline to any class two pages share by accident rather than by design.
- **Spacing composes `--space-*`, and departing from it is meant to be visible.** `18-spacing-scale` tokenised the rhythm the pages had converged on — 2/4/8/12/16/20/24/32, every value already in three or more stylesheets — and changed no pixel: 170 of the 207 px-bearing spacing declarations became `var(--space-N)`, and the other 37 kept their literals. The names carry the values (`--space-12` *is* 12px) because the objection the project used to raise was against *index* names like `--space-3`, which hide the value at every read. **The rule is per declaration, not per length: a declaration is tokenised only if every length in it is on the scale**, so `padding: 10px 12px` stays wholly literal rather than becoming a half-tokenised shorthand. What stays literal is control internals — the input/select padding (`10px 12px`), the pill paddings, `.btn`'s `8px 14px`, the accent-bar rows short by the 3px the bar occupies, the portal card's chevron gutter — tuned to type metrics and specific geometry, the same carve-out `tokens.css` already makes for the data-visual radii. So a bare `10px` in a spacing declaration now means "deliberately off the scale", and a new `13px` stands out in review. The scale is **not themed and not responsive**: no `--space-*` is redeclared in the dark block or in a media query, because that would make one `var(--space-24)` mean two things. A page that tightens at a breakpoint still declares its own measured value on its own selector.
- **Only `tokens.css` and `base.css` are read by everyone.** Do not add to either to solve one page's problem.
- **Never write a raw colour outside `tokens.css`** — it cannot follow the dark palette, and nothing catches it until someone switches theme. There are exactly **two** sanctioned exceptions, both the same value and both unavoidable: `index.html`'s pre-paint script and `src/theme.js` each carry the light and dark `--bg` literals, because `<meta name="theme-color">` cannot read a custom property. Both `--bg` declarations in `tokens.css` warn about it. Change all three together.
- **The theme is chosen, not inherited.** The dark palette is `:root[data-theme='dark']`, not a `prefers-color-scheme` media query; a classic inline script in `index.html` resolves the stored choice (light / dark / absent-means-system) before first paint and writes the attribute. A `type="module"` script is deferred and a `useEffect` runs after React has painted, so **neither can apply a theme without a visible flash** — if that script is ever moved or modularised, the flash comes back on every page.

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

# Create the collections, validators, and indexes. Idempotent, and safe to
# re-run: an index that has drifted from database/schema.py is dropped and
# rebuilt rather than aborting the run. --dry-run prints the plan and writes
# nothing, which is worth doing first on a database that has real data in it.
flask init-db --dry-run
flask init-db

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

### Which changes take the full pipeline, and which do not

The middle two steps are for **backend vertical slices** — an endpoint, an auth
boundary, a database write, anything `pytest` can pin. Specs `01`–`11` all took
them, and so did `20` — the first backend slice since — and they earned their
place: the review pipeline caught a real provenance bug in `08`, three accepted
hardening changes in `10`, and four accepted refinements in `20`.

**Frontend-only work has taken a different route since `12`, and that is
deliberate rather than drift.** There is no frontend test runner in this
project, so `/test-feature` has nothing to run, and the risk in a CSS or layout
change is not the kind a diff read surfaces: it is a header height, a contrast
ratio, a wrapped line, a key that does not close a panel. Specs `12`–`17` were
verified in a browser instead — measured at named widths, driven with real
keypresses — and the findings that mattered came from those measurements, not
from reading. `17` is the clearest case: the defect it found (collapsing a
one-item nav made that header 17px **taller**) is invisible in the diff and
obvious on the second row of a measurement table.

So:

- **Backend or full-stack slice** → the full pipeline above.
- **Frontend-only** → spec, implement, verify in a browser against the spec's
  own Definition of done, record the measurements in the stylesheet, commit.
  Write the numbers down; they are the evidence, and the next spec reads them.

**Go back to `/code-review-feature` when the blast radius exceeds what you can
directly verify** — a change touching many page files at once, or one that
reaches back into shared behaviour. A spacing scale is exactly that case and
should say so in its own spec. "It is frontend" is not on its own a reason to
skip a review; "I measured every surface it touches" is.

---

## Implemented vs stub features

Keep track of incomplete or intentionally stubbed features as the project develops.

| Feature | Status |
|---|---|
| Database setup | Implemented — `database/schema.py` declares every collection, `$jsonSchema` validator, and index as plain data; `flask init-db` applies it idempotently and is never run at startup, so an unreachable MongoDB still lets the app serve `/api/health`. **`20-init-db-index-reconcile` made it survive a drifted database:** MongoDB will not reshape an index in place, so a live index whose keys, uniqueness, or options no longer match its declaration used to raise `IndexKeySpecsConflict` and skip every collection after it. `database/indexes.py` (pure — no pymongo, no Flask) now plans each declared index against `index_information()`, and `init_db.py` applies that plan in check→drop→create order, one index at a time. **Only an index colliding with a declared one is ever dropped** — the planner walks the declarations and never the live indexes, so `_id_` and an operator's ad-hoc indexes are unreachable rather than filtered. A unique index is never dropped on faith: duplicates are looked for first and a hit is reported rather than dropped, so a failed rebuild cannot leave a collection unconstrained. That pre-check also guards a *first* creation, since `create_index(unique=True)` over bad data would abort the run the same way. `--dry-run` previews the plan while writing nothing (it still reads, so it can warn that a real run would be blocked). `background` and `ns` are treated as inert so an old catalog entry does not trigger a pointless rebuild. Reporting lives in `database/reporting.py`, mirroring `notifications/reporting.py`. Deferred: undeclared indexes are never tidied up (making the declared set correct is not the same as owning the whole index space), validator drift is not compared (`collMod` reapplies in place and never conflicted), and the check-then-write race is accepted rather than locked. |
| Authentication | Implemented — Flask-JWT-Extended login/refresh/logout/me via HttpOnly cookies, role-based `@role_required`, `flask create-admin` bootstrap. No public registration. |
| Academic hierarchy | Implemented — CRUD REST APIs for all five levels (`/api/institutes`, `/api/departments`, `/api/semesters`, `/api/courses`, `/api/classes`); writes are admin-only, reads are admin + faculty. Parents are verified on create and deletes are blocked (never cascaded) while children exist. Admin UI at `/admin/academics`. Faculty assignment and student enrollment are now handled by Admin user management (below). |
| Admin user management | Implemented — admin-only `/api/users` CRUD (create/list/get/update, `PUT .../status`, `PUT .../password`), plus `PUT /api/classes/<id>/faculty` and `/api/classes/<id>/students` for enrollment. Accounts are deactivated, never deleted; `role` is immutable after creation; an admin cannot deactivate themselves or the last active admin. Admin UI at `/admin/users`, with the class assignment panel inside `/admin/academics`. Deferred: pagination on the user list, and invalidating an already-issued access token on password reset/deactivation (bounded by the 15-minute access-token lifetime). |
| Face enrollment | Implemented — admin-only `/api/students/<id>/face-encodings` (list/register/delete one/delete all) plus `GET /api/classes/<id>/face-enrollment` for per-student sample counts across a roster. Stores only the 128-value encoding in the `face_encodings` collection; the source image is processed in memory and never persisted, and the encoding vector never appears in a response. All CV code is confined to `backend/recognition/encoder.py`, which imports `face_recognition`/`numpy` lazily — where `dlib` is not built, the app still starts and these endpoints return `503`. Admin UI at `/admin/face-enrollment` (file upload + webcam capture). Deferred: students/faculty cannot see enrollment status, and changing the detector model has no re-encoding migration path (the stored `model` field is what makes one possible later). Bulk import left this row and became its own feature — see below. |
| Bulk face enrollment import | Implemented — admin-only `POST /api/classes/<id>/face-enrollment/import`, multipart with up to `MAX_IMPORT_FILES` (10) `images` parts, returning a per-file report rather than a bare success. **The server resolves identity, never the client**: the request carries files and nothing else — no student id, no mapping — and `recognition/filenames.py` (pure: no Flask, no pymongo, no CV) matches each file's stem to a student *on that class's roster only*, **by student ID** — the roll number a college already names its photo folders by, `24DCS001.jpg`, matched case-insensitively. **There is no `student_id` field on `users`**: `email` is that collection's only unique identifier and the ID is its local part (`24dcs001@charusat.edu.in` → `24DCS001`), so `filenames.py::student_id` is the whole of the project's notion of one and is the single place to change if a real roll-number field is ever added. The full email is also accepted but deliberately unadvertised: it exists as the escape hatch for the one case an ID cannot resolve — two students colliding on an ID across different email domains, impossible within one institute's roster and possible across two — and the ambiguous message is the only place it is named. The rule is otherwise deliberately dumb, and the omissions are the design: no numeric suffix is stripped (`24DCS001-2.jpg` matches nothing, because `-2` cannot be told from a real ID), names are not a key (two students may share one), and a collision is reported `ambiguous` rather than guessed at. **Partial success is the normal outcome**: `recognition/bulk_import.py` calls the existing `register_encoding` once per matched file so the five-sample cap, the one-face rule and the cross-student duplicate check apply exactly as they do to a single upload — including against a sample inserted earlier in the same batch, since successes commit as they go and there is no transaction. Only `ValidationError`/`ConflictError` are caught per file (becoming a `rejected` row); a `PyMongoError` still reaches the blueprint as a `500`, so an outage never disguises itself as a bad JPEG. Four statuses (`registered` / `no_match` / `ambiguous` / `rejected`) inside one `200`, because the request was well-formed and fully processed and the report *is* the body. `source` is fixed server-side as `upload`, the encoding vector cannot reach the response (literal allow-lists, as everywhere in `recognition/serializers.py`), and a file name — which here **is** a student's roll number, identifying them as surely as an email does — is never opened, never joined to a path, and never logged. **The 26 MB request ceiling was not raised**: `MAX_IMPORT_FILES × MAX_IMAGE_BYTES` deliberately exceeds it, and the frontend packs batches under a 20 MB budget, sending them sequentially so progress is reportable and one failure costs one batch. UI on `/admin/face-enrollment` above the roster; a failed batch keeps the rows already collected and says how many were never attempted. Deferred: no ZIP or CSV-manifest input (both rejected in the spec — an archive means owning decompression-bomb and traversal defence in the one feature that writes biometric data), no naming convention for several samples per student (import several folders instead), no same-student duplicate detection so re-running a folder adds near-identical samples until the cap refuses them, no import audit trail, no background or asynchronous processing (encoding is synchronous and holds a worker), no rate limiting, and bulk import stays admin-only. |
| Admin portal | Follow current implementation status |
| Faculty portal | Partial — the dashboard links to attendance capture and attendance history (below). Nothing else on it is built yet. |
| Student dashboard | Implemented — student-only `GET /api/attendance/me` (per-class + overall standing for every enrolled class, plus the configured attendance `threshold` and a per-class `meets_threshold`/`lectures_to_reach`) and `GET /api/attendance/me/sessions?class_id=` (that student's own status per lecture, plus a month-by-month trend, the same three threshold fields, and optional inclusive `from`/`to`). Both identify the student from the JWT — **no endpoint anywhere takes a student id**, so another student's attendance is unreachable rather than merely forbidden. An unenrolled class is `403`, a missing one `404`. The denominator is the student's own records, never the class's session count, so a student enrolled late is not marked down for lectures held before them; `percentage` is `null` (not `0`) when nothing has been recorded, and `meets_threshold`/`lectures_to_reach` follow it to `null` rather than `false`/`0`. The bar is applied per class only — `overall` and every `monthly` bucket carry none of the three threshold fields — and a misconfigured `LOW_ATTENDANCE_THRESHOLD` degrades the read path to `threshold: null` rather than failing the request. Responses deliberately omit `marked_by`, `source`, `taken_by`, `updated_by`, session ids, and anything about another student. Reads only — nothing is written and no count, percentage, or comparison is stored. Student UI at `/student/attendance`, with four hand-built visuals (overall bar, per-class bars weakest-first, monthly trend, lecture strip) and no charting library; each per-class bar additionally carries a marker at the required percentage and a `ThresholdNote` stating met/below in words, plus a catch-up figure when below. Deferred: faculty/admins still cannot view a named student's attendance; no export; no per-lecture explanation of why a student was marked absent. |
| Face recognition (attendance matching) | Implemented — `recognition/matcher.py` matches faces detected in a capture against the roster's encodings only, collapsing duplicate claims to the best distance and counting everything else as an unknown face. `recognition/frames.py` is the only `cv2` importer and samples up to 8 frames from a video; both CV libraries stay lazily imported, so a server missing either still starts and only the affected path returns `503`. |
| Attendance system | Implemented — faculty-only `/api/classes/assigned`, `POST /api/attendance/recognize` (proposes, writes nothing), `GET /api/attendance/session`, and `POST /api/attendance` (saves a reviewed list). Every class-scoped endpoint additionally requires `classes.faculty_id` to be the caller (`403` otherwise, including for an unassigned class). Stores `attendance_sessions` (one per class per date, unique index, `date` at UTC midnight) and `attendance_records` (one per student, absences explicit, `marked_by` recording whether recognition or a human decided). The captured photo/video is never persisted — the video temp file OpenCV needs is deleted in a `finally`. Faculty UI at `/faculty/attendance`. Deferred: admins cannot take attendance; no live/streaming recognition; and recognition runs synchronously in the request, so a large video occupies a worker. |
| Faculty attendance history | Implemented — faculty-only `GET /api/attendance/sessions` (one owned class at a time, optional inclusive `from`/`to`, `limit` default 50 / max 200, newest first, counts from one `$group` aggregation and no `records` array), plus `GET`/`PUT`/`DELETE /api/attendance/sessions/<id>`. Ownership is reached through the session, so an unknown id is `404` and another faculty member's session is `403`. An edit changes statuses only (`class_id`, `date`, `source`, `created_at`, `taken_by` immutable) and is validated against the students already in the session rather than today's roster; it stamps the optional `attendance_sessions.updated_by`, while `edited` is derived from `updated_at > created_at`. A delete removes the records first, then the session, and is not recoverable. No CV import on any of these paths. Faculty UI at `/faculty/attendance/history`. Deferred: admins cannot view or edit attendance; no cross-class or institute-wide view; and there is no per-edit audit trail beyond who last changed it and when. Export left this row and became its own feature — see below. |
| Attendance export (CSV + PDF) | Implemented — faculty-only `GET /api/attendance/export/csv` and `GET /api/attendance/export/pdf`, one owned class at a time with optional inclusive `from`/`to`. **Two formats because they answer different questions**, not two encodings of one: the CSV is the register (a row per student per lecture — `date,student_name,student_email,status,marked_by,source`, absences explicit, `\r\n`, `utf-8-sig` so Excel on Windows reads a non-ASCII roster), and the PDF is the report (title block with the hierarchy and provenance, one row per student with present/absent/total/percentage, a `below N%` marker, then a lecture index). Neither writes anything and neither reaches data the caller could not already fetch through the history endpoints. `_export_sessions` is the single choke point for `require_owned_class` and for `MAX_EXPORT_SESSIONS` (400), which **refuses rather than trims** — a silently short export is indistinguishable from a term with fewer lectures. The PDF's figures come from `attendance/threshold.py` unwrapped, so a printed report cannot disagree with the student's own dashboard or the low-attendance email; the report lists the **union** of the roster and everyone holding a record in range, and a student's denominator is their own record count, never the lecture count (`09`'s rule). A misconfigured threshold degrades to no marker rather than failing. Filenames are built from an `[a-z0-9-]` whitelist in `attendance/export_naming.py` — that whitelist *is* the `Content-Disposition` injection defence — and every CSV cell beginning `= + - @` tab or CR is apostrophe-guarded against spreadsheet formula execution. `reportlab` is the one new dependency, lazily imported in `attendance/pdf_export.py` and nowhere else (import-isolation test alongside the `cv2` and `face_recognition` ones), so a server without it still starts, still exports CSV, and answers `503` on PDF only. Faculty UI on `/faculty/attendance/history`. Deferred: admins and students cannot export; no per-lecture register in the PDF and no summary shape in the CSV; no export audit trail; **the PDF's standard fonts cover Latin-1 only** — a name outside it is substituted to `?` with a note pointing at the CSV, rather than reportlab's own silent ZapfDingbats black squares (no bundled Unicode TTF); and both responses are assembled in memory rather than streamed. |
| ML prediction | **Cancelled — not part of this project.** No model, no training, no `scikit-learn`, and no marks/grades/assessment data to train on. Do not propose or build it. |
| Notifications | Implemented — `flask notify-low-attendance` emails students whose recorded attendance is below `LOW_ATTENDANCE_THRESHOLD` (default 75) in a class they are still enrolled in. **CLI-only by design: no route, no blueprint, no React screen**, so nothing on a request path can send mail; `test_app_factory.py` still asserts no route contains `notification`. The bar is applied **per class**, never to an overall average, and to the same rounded figure `GET /api/attendance/me` shows — a class under `MIN_RECORDED_LECTURES` (5, a constant in `notifications/service.py`) is skipped so nobody is mailed "0%" after one lecture. One email per student lists every class they are short in; it is plain text with no HTML part and no link, carries only that student's own figures, and states a recorded percentage against a configured bar — never a consequence, a prediction, or a risk score. Sends are recorded one row per class in `attendance_notifications`, which is what `NOTIFICATION_COOLDOWN_DAYS` (default 7) reads to avoid mailing the same student about the same class twice; the index is deliberately non-unique so a still-short student can be warned again later. **Send first, then record** — a failed send writes nothing and is retried next run, and one bad address never stops the sweep. `--dry-run` lists recipients while opening no SMTP connection and writing nothing. No new dependency: stdlib `smtplib`/`email`, confined to `notifications/mailer.py`. Deferred: no admin trigger or admin-set threshold (an optional later spec), the bar is global rather than per-institute or per-course, there is no HTML mail, no unsubscribe, no per-run audit beyond the sent rows, and the sweep runs synchronously in one process. |
| App shell | Implemented — `AppShell.jsx` is the root of all nine signed-in pages, providing the skip link, the `<header>` (brand, role nav, user name, logout), the `<main id="main">` landmark, and the page's single `<h1>` from its `title` prop. `NavBar.jsx` marks the current route via `NavLink` (which sets `aria-current="page"` itself), with `end` on every link so the two nested faculty routes cannot both claim to be current. `src/navigation.js` is the **only** place any role's link list is written — the header nav and the three landing pages both read it, so they cannot disagree. Role filtering there decides what is *shown*, never what is allowed: `ProtectedRoute` and `@role_required` are untouched. Removed nine copies of `<h1>`/`Welcome, …`/logout and six hand-rolled back links. `Login.jsx` and `NotFound.jsx` are reachable while signed out, render no shell, and were not touched. **The CSS lives in `styles/shell.css`**, which was written as deliberately temporary and is the one file the redesign did not replace: the header survived every group largely as-is. Its collapsible-nav deferral was paid off by `17-collapsible-nav`, and the file no longer describes itself as scaffolding. `.portal-*` was deleted from it in group 4a once `/admin` took its last renderer. The component vocabulary it deliberately did **not** define was extracted later, once two groups had shown what repeats — see `styles/components.css` and the "Stylesheets" section above. **The nav collapses behind a Menu button at `max-width: 413px`**, which took the phone header from three rows to two (125.5px to 108.2px at 360, the `<h1>` from 166px down the screen to 148). It is a disclosure and deliberately not a dialog — no focus trap, no `aria-modal`, no backdrop, no focus move on open; Escape closes it and only takes focus back when focus was inside the panel, and a route change or a link click closes it. Whether the list is *shown* is decided entirely by CSS, so the desktop nav does not depend on component state. 413 comes from faculty, not admin — "Take Attendance / Attendance History" is the widest link list — and a **one-destination nav does not collapse at all** (`.app-nav--single`), because the 44px button is taller than the 26px link it would hide, which measured 17px *worse* for students. **The user block carries a three-state theme control** (`ThemeToggle.jsx`, a `.form-field` select: System / Light / Dark), added by `19-theme-toggle` — the last of the shell's deferrals. It is the header's third flex child still, not a fourth: the control went *inside* `.app-user` for the same reason `17` put the nav toggle inside `<nav>`. Paying for its 105px cost took two changes — `.app-user`'s gap 12→8, and the header's column gap 24→12, the latter free because `space-between` makes that gap a minimum only reached at the wrap edge (identical pixels at all ten measured widths, it only moves the edge). Result: 27 of 30 role×width cells are pixel-identical to `main`, 768/1024/1440 included. Three are not, and are deliberate: the one-row threshold moves 677→750 (admin), 689→762 (faculty), 526→599 (student), so the band just above the old threshold gains a row, and student's 414–480 header goes 57.1→92.2px. A 105px control does not fit a row that had 90px of slack; the choice was only which widths pay. `.app-user .btn { flex: none }` was added with it — without it a 320px row took its shortfall from the button, wrapping "Log out" onto a second line. Still deferred on purpose: no `.table` primitive and no `.modal` (`ConfirmDialog` is one component used six times, not two independent implementations, so it got `styles/confirm-dialog.css` instead), no breadcrumbs, tabs, or global search. Its spacing-scale deferral was paid off by `18-spacing-scale`, and the file's eleven spacing declarations now compose `--space-*`. The shell did get narrow-width fixes during group 3. |

**Do not implement an unfinished feature unless the active task explicitly targets it.**

Update this section when major features become implemented.

### Next planned feature

Specs `01`–`22` are built. Every core capability this file describes exists, `12-app-shell` added the frame the UI work sits inside, and `13-component-vocabulary` added the primitives the remaining pages are built from.

**`22-bulk-face-enrollment-import` is the most recent.** A backend slice with a user-facing half, so it took the full pipeline *and* browser measurement, like `21`. It added no dependency and changed no collection: a bulk-imported sample is byte-for-byte what the single-upload path already wrote, and the `source` enum deliberately did **not** grow an `import` value, because how the admin operated the UI is not a fact about where the biometric data came from.

Three things about it are worth carrying forward. **The identity rule got its own pure module** (`recognition/filenames.py`) rather than living in the orchestration, because deciding which student a face belongs to is the most consequential rule in the feature and it should be readable and testable without a database. **The bulk path re-used `register_encoding` unwrapped** rather than reimplementing any per-file rule — the failure mode it avoids is a second identity for one face, and the test that matters most proves the cross-student check still fires against a row inserted moments earlier in the same request. **And its UI was verified by driving the real component with a stubbed `fetch`**, not by reading the diff: that found a defect the diff could not show — the result group's heading read `12Registered` to a screen reader, because a flex gap separates the count from the word on screen but leaves no whitespace in the text. The same run proved the batching (12 files → 10 + 2; 4 × 7 MB → 2 + 2, so the byte budget splits independently of the file cap) and that a failed second batch keeps the first batch's rows on screen. Its `/code-review-feature` pass returned no high- or medium-severity findings; the one item applied from it moved the file-count check ahead of reading any part's bytes, because the validator's docstring already claimed that ordering and was therefore wrong.

**`21-attendance-export` is the one before it.** A backend slice with a user-facing half, so it took the full pipeline *and* browser measurement — the first feature to need both since the frontend-only run of `12`–`19`. It is also **the one spec that deliberately carries two features**, CSV and PDF, against this file's own preference for single-purpose specs: they share the endpoint shape, the owner check, the date-range validation, the session cap, the filename rules and the whole frontend path, and differ only in the module that turns rows into bytes. It brought in `reportlab`, the first new dependency since `07`. See its row in the table above.

**`20-init-db-index-reconcile` is the one before it, and it is not part of any of the groups below.** Also a backend slice, but with no UI at all, deliberately: `flask init-db` is run from a terminal by whoever administers the deployment, and the only screen it could plausibly have grown is a "drop an index on the production database" button. That is the same call `10-low-attendance-notifications` made.

**Next: unscheduled.** The deferred items below are what remains, none of them picked. The two that were named as likely candidates before `21` — and still unpicked after `22` — are a faculty/admin view of a *named* student's attendance (ruled out as "small" — see below) and password change / forgot-password, which is a larger piece of work than its name suggests: it needs a token store, an email path that is currently CLI-only by design, and a decision about invalidating already-issued access tokens that `05` deferred.

**The redesign is finished.** Every page was replaced through `/frontend-maker`, in small related groups, and `scaffolding.css` is deleted:

1. ~~**Login** — `/login`~~ — `styles/login.css`.
2. ~~**Student** — `/student` + `/student/attendance`~~ — `styles/student-dashboard.css`, `styles/student-attendance.css`.
3. ~~**Faculty** — `/faculty` + `/faculty/attendance` + `/faculty/attendance/history`~~ — `styles/faculty-dashboard.css`, `styles/faculty-roster-row.css`, `styles/faculty-attendance.css`, `styles/faculty-history.css`.
4. ~~**Admin** — `/admin` + `/admin/academics` + `/admin/users` + `/admin/face-enrollment`~~ — run across two sessions (4a: `styles/admin-portal.css`, `styles/admin-academics.css`; 4b: `styles/admin-users.css`, `styles/admin-face-enrollment.css`), then a teardown step that extracted `.pill--neutral`, moved `ConfirmDialog` into `styles/confirm-dialog.css`, and **deleted `scaffolding.css`** — 396 lines to 0.

**How the teardown worked, since the pattern is worth keeping.** A page never restyled a borrowed class; it renamed its own usages onto page hooks and left the shared rule alone, so a rule died only when its last renderer moved. Group 4a deleted `.portal-*` from `shell.css` and `.hierarchy-select`/`.is-selected` from `scaffolding.css`; `/admin/face-enrollment`, as the last renderer of thirteen more, took the file to 90 lines; the final step took it to zero. Where a selector list was shared (`.hierarchy-level button, .dialog button`), only the dead half was removed.

**The three decisions group 4b was left to settle, and what it decided:**

- **The neutral `.pill`** — extracted as `.pill--neutral`, from **three** implementations rather than two. Each page wrote it page-scoped and reported it; the extraction waited so `.ah-flag`, `.au-flag` and `.fe-flag` could migrate in one edit. `flex: none` deliberately stayed on each page hook — it is how a page arranges the flag, not what the flag is. Role pills were considered and rejected: forty bordered badges down a directory is a texture, not a mark.
- **`(pointer: coarse)`** — adopted as a project pattern, used by `admin-academics.css`, `admin-users.css` and `admin-face-enrollment.css`. The paired width breakpoint is measured per page, never copied: `/admin/users` measured 594 and chose 480, `/admin/face-enrollment` measured 380 and also chose 480, for opposite reasons. Both are written into the files with their measurements.
- **The form-hygiene batch** — applied across all three admin pages at once, together with the accessibility items its audit surfaced: `name` on sixteen controls, `spellCheck`/`autoComplete` on the email-bearing fields, `aria-live` regions for success and loading states, focus-on-error in `UserForm`, `Intl.DateTimeFormat` for sample timestamps, and `aria-label` on both camera previews.

**The polish pass is finished.** Agreed 2026-08-26 and scoped 2026-08-28 to exactly four items, each its own spec and its own session — `14` the focus trap, then the contrast fix, then `.card--link`, then the shell items. Everything below the four is still unscheduled and still needs asking about. ~~`14-confirm-dialog-focus-trap`~~ is done: `ConfirmDialog` now traps Tab, and returns focus to the trigger or, where confirming deleted the trigger's row, to `<main>`. ~~`15-muted-on-accent-contrast`~~ is done: `--text-muted` on `--accent-weak` (4.47:1 light / 4.49:1 dark) was under the floor, and `.fh-session--open .fh-session-facts` was the only place left rendering it — it moves to `--text` (6.30 / 5.89) on the page's own selector, the same correction `.au-email` and `.fe-email` already carried. An audit of every `--accent-weak` fill closed the set at those three; the warning now sits beside the token in `tokens.css`, where the next page to tint a row will meet it. ~~`16-portal-card-link`~~ is done: the card whose whole area is one nav target — built three times, ~90 identical lines each — is now `components/layout/PortalCard.jsx` plus `styles/portal-card.css`, rendered by all three landing pages, which keep only their grids. It went to a component file rather than `components.css` because the shape is four coupled classes joined by `:has()` and a page cannot compose half of it, and it took the JSX with it rather than being a CSS-only extraction, since the three routes were each writing the same eight lines. `/student` converged onto the other two on all four values it differed by (gutter 56→48, chevron 24→20, breakpoint 480→420, description 0.9rem→0.85rem/1.45); measured at eleven widths, its description holds its line count everywhere, `/faculty` and `/admin` are identical to the byte, and the explicit `line-height` additionally fixed a 26.1px line box inherited from the `font: 18px/145%` shorthand. What remains, in rough order of substance:
- ~~**Shell items deferred on purpose**~~ — **all three are done, and the polish pass is complete.** ~~The theme toggle~~ is done: `19-theme-toggle` replaced `tokens.css`'s `@media (prefers-color-scheme: dark)` with `:root[data-theme='dark']`, resolved before first paint by a classic inline script in `index.html`, and put a three-state System/Light/Dark select in the header's user block. **Three states and not two**, because a boolean switch would have silently deleted "follow the OS", which was every user's behaviour; System is stored as the *absence* of the key, so never-chosen and chose-System are one state. The palette is written once rather than twice — keeping the media query *and* an override needs the dark block duplicated as `:root:not([data-theme='light'])` and again outside it, in the file whose whole job is to be the single place a colour is written; the price is that System watches the OS with a listener instead of getting it free. `color-scheme` became explicit per theme so scrollbars and the three date inputs follow the choice rather than the OS, and the two media-scoped `theme-color` metas collapsed to one the script sets. No token value moved: proved by a normalized source diff against `main` (only four lines differ, all intended) and by resolving all 28 tokens from both stylesheets in both themes — 0 differences, with a negative control that fires. It did **not** take `/code-review-feature`, and the spec says why in as many words: unlike `18`'s unseeable 170-declaration substitution, this is directly observable, and the values did not change at all. ~~The collapsible nav~~ is done: `17-collapsible-nav` took the phone header from three rows to two. ~~The spacing scale~~ is done: `18-spacing-scale` added `--space-*` to `tokens.css` and tokenised 170 of the 207 px-bearing spacing declarations across sixteen stylesheets, changing no pixel — the 37 that are control internals rather than layout kept their literals on purpose. It is the one polish spec that took `/code-review-feature`, per the blast-radius rule; equivalence was proved by reverse-substituting the tokens back to literals and diffing against `main` (total, not sampled), and confirmed in a browser by applying every changed rule to a probe element and diffing the full computed style — 67,165 comparisons, 0 differences, with a negative control showing an undeclared reference collapses silently to `0px`. The fourth polish item was three separate deferrals in one bullet and became three specs rather than one, so the four-item pass runs to `19`.
- **Guideline findings reported and not applied**: Title Case on buttons and headings (declined so far — the project uses sentence case uniformly, so changing two pages would be worse than changing none), URL-reflected filter/picker state on the two admin list pages, and list virtualization.
- **The picker on `/admin/face-enrollment` cannot fully fit the longest department name**, and it is arithmetic rather than an oversight: three selects wide enough for it need 851px against an 820px measure. `flex-basis` was raised to 200 and the cap lifted at 480, which fixes it below 440 and at 636 and improves desktop from 171px to 241px of text room; it is still ~10px short at 1024+ and clipped in the 481–820 band. Closing it means two-per-line at desktop or the picker outgrowing the roster below it.

Beyond that, what remains is only the deferred items already recorded per feature above, none of them scheduled:

- An admin trigger with an admin-set threshold for notifications — the alternative `11` considered and did not choose, since it needs a new blueprint, an admin route, and the threshold moving to MongoDB, and it would undo the deliberately-kept `"notification"` route guard.
- A faculty/admin view of a *named* student's attendance — deliberately ruled out as a "small" feature: `09` built things so that **no endpoint anywhere takes a student id**, and undoing that is a security decision, not a finishing touch.
- User-list pagination and live/streaming recognition. **Bulk face-enrollment import is off this list** — `22` shipped it; what it deferred in turn (ZIP or manifest input, a multi-sample naming convention, same-student duplicate detection, an import audit trail, background processing, rate limiting, and an admin-only boundary) is recorded in its own row above. **Both exports are off this list too** — `21-attendance-export` shipped CSV and PDF together; what it deferred in turn (an admin or student export, the other shape in each format, an export audit trail, a Unicode font for the PDF, streaming) is recorded in its own row above.

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