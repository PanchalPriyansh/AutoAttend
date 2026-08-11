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
| Academic hierarchy | Follow current implementation status |
| Admin portal | Follow current implementation status |
| Faculty portal | Follow current implementation status |
| Student dashboard | Follow current implementation status |
| Face recognition | Follow current implementation status |
| Attendance system | Follow current implementation status |
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