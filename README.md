# AutoAttend

Automatic attendance management for a college, built around face recognition.

Calling the roll by hand costs several minutes of every lecture, and a student
whose attendance is slipping usually finds out too late to do anything about
it. AutoAttend addresses both: a faculty member marks a whole class from one
photo or short video of the room, students can see their own standing whenever
they want, and anyone who drops below the required percentage is emailed while
there is still time to catch up.

**Attendance is the only student data this project records.** There are no
marks, no grades, no assessments, and no risk prediction. The machine learning
here is the face recognition itself — a low-attendance email is a plain
threshold check against recorded attendance, not a model output.

---

## What it does

Three roles, each with its own portal. Accounts are created by an admin;
**there is no public sign-up page, and adding one is out of scope.**

| Role | Can do |
|---|---|
| **Admin** | Manage the academic hierarchy, create and deactivate users, assign faculty and enroll students, register student faces |
| **Faculty** | Take attendance for an assigned class from a photo or video, review the proposed list before saving, then browse and edit past sessions |
| **Student** | See their own attendance per class and overall, a month-by-month trend, and how many lectures it would take to get back above the bar |

Two capabilities do the actual work:

- **Face-recognition attendance.** Faculty pick the academic context
  (institute → department → semester → course → class → date) and upload a
  classroom image or video. Faces are matched against the encodings of the
  students on *that roster only*; duplicate claims collapse to the closest
  match and anything else is counted as an unknown face. The result is a
  proposal — nothing is written until a human reviews and saves it. The
  captured photo or video is never stored.
- **Low-attendance email.** `flask notify-low-attendance` finds students below
  the configured percentage in a class they are still enrolled in and emails
  each one a single message listing every class they are short in. It is a CLI
  command by design: no route can send mail.

---

## Built with

- **Frontend** — React 19, Vite, React Router
- **Backend** — Flask REST API, Flask-JWT-Extended (HttpOnly cookies + CSRF)
- **Database** — MongoDB
- **Recognition** — `face_recognition` (dlib), OpenCV for video frames, NumPy
- **Email** — the Python standard library, `smtplib` and `email`

---

## Getting started

You will need **Python 3.11+** (developed on 3.14), **Node `^20.19` or
`>=22.12`** (what Vite requires), and a **MongoDB** you can connect to — a
local server or an Atlas cluster both work.

### Before you install: the dlib build

`face_recognition` depends on **dlib, which compiles from source** and needs a
C++ toolchain. On Windows, install **CMake** and the **Visual Studio C++ Build
Tools** *before* running `pip install`, or that one package will fail.

If you would rather not deal with it right now, you don't have to. The
recognition libraries are imported lazily, so **everything except face
enrollment works without dlib** — the API starts, the full test suite passes,
and only the face-enrollment endpoints report `503` until it is built.

### Backend

Run these from the repository root:

```bash
python -m venv venv

# Windows
venv\Scripts\activate
# Linux / macOS
source venv/bin/activate

pip install -r backend/requirements.txt
```

Create a `.env` file in the repository root — see
[Configuration](#configuration) below for the variables. At minimum you need
`MONGODB_URI`, `JWT_SECRET_KEY`, and `JWT_COOKIE_SECURE=false` for local
development over http.

Then, from `backend/`:

```bash
cd backend

# Create the collections, validators and indexes. Safe to re-run.
flask init-db

# Create the first admin. There is no sign-up page, so without this
# there is no way to log in.
flask create-admin

python app.py
```

The API is on `http://localhost:5000`, and `GET /api/health` reports whether
it can reach the database.

### Frontend

From `frontend/`, in a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173` and sign in with the admin you just created. The
login redirects each user to their own portal.

> The frontend talks to the API cross-origin with credentials, so the backend's
> `CORS_ORIGINS` must name the exact origin the frontend is served from. The
> defaults on both sides already line up for local development.

---

## Configuration

Everything is read from the environment, via a `.env` file in the repository
root. **Never commit it** — `.gitignore` already excludes it.

`MONGODB_URI` and `JWT_SECRET_KEY` have no fallback on purpose: the app raises
at startup rather than connecting to nothing or signing tokens with a default
key.

| Variable | Default | What it does |
|---|---|---|
| `MONGODB_URI` | *(required)* | MongoDB connection string |
| `MONGODB_DB_NAME` | `autoattend` | Database name |
| `JWT_SECRET_KEY` | *(required)* | Signs access and refresh tokens |
| `JWT_COOKIE_SECURE` | `true` | Set to `false` for local http, or the browser drops the auth cookies |
| `FLASK_ENV` | `production` | `development` turns on debug |
| `PORT` | `5000` | Port the API listens on |
| `CORS_ORIGINS` | `http://localhost:5173` | The one origin allowed to call the API with credentials |
| `LOW_ATTENDANCE_THRESHOLD` | `75` | The percentage a student must clear, per class |
| `NOTIFICATION_COOLDOWN_DAYS` | `7` | How long a student is left alone about one class after being mailed |
| `SMTP_HOST` | *(needed to send)* | Mail server |
| `SMTP_PORT` | `587` | Mail server port |
| `SMTP_USERNAME` | *(needed to send)* | Mail account |
| `SMTP_PASSWORD` | *(needed to send)* | Mail password — an app password, not your login |
| `SMTP_USE_TLS` | `true` | STARTTLS; anything but an explicit `false` keeps it on |
| `SMTP_FROM_ADDRESS` | *(needed to send)* | Envelope sender |
| `SMTP_FROM_NAME` | `AutoAttend` | Display name on the email |

The frontend reads one optional variable of its own, `VITE_API_BASE_URL`,
which defaults to `http://localhost:5000`.

The `SMTP_*` values are only needed to actually send. You can preview who would
be emailed without configuring any of them — see below.

---

## Command reference

Backend commands run from `backend/` with the virtualenv active.

```bash
flask init-db                          # collections, validators, indexes
flask create-admin                     # the first admin, interactively

flask notify-low-attendance --dry-run  # list who would be emailed; sends and
                                       # writes nothing, needs no SMTP config
flask notify-low-attendance            # actually send

pytest                                 # the full suite (1001 tests)
pytest tests/test_auth_routes.py       # one file
pytest -k "test_name"                  # one test
```

Frontend commands run from `frontend/`:

```bash
npm run dev       # dev server on :5173
npm run build     # production build
npm run preview   # serve the build
npm run lint      # oxlint
```

There is no frontend test runner; frontend changes are verified in a browser.

---

## Layout

```text
AutoAttend/
├── backend/
│   ├── app.py            Flask app factory and the three CLI commands
│   ├── config.py         every environment variable, read in one place
│   ├── routes/           HTTP concerns only, thin handlers
│   ├── auth/  academic/  users/  attendance/  notifications/
│   │                     the business logic those routes call
│   ├── recognition/      all CV code, and the only place it lives
│   ├── database/         connection, schema, indexes
│   └── tests/            pytest
│
└── frontend/
    └── src/
        ├── routes/       one file per page
        ├── components/   layout, admin, faculty, student
        ├── api/          wrappers over fetch, one per resource
        └── styles/       one stylesheet per page; index.css is the manifest
```

Academic data follows a strict hierarchy — **institute → department →
semester → course → class → student** — and every level of it lives in
MongoDB. None of it is hard-coded.

---

## Contributing

[`CLAUDE.md`](CLAUDE.md) is the working brief: architecture, code style, the
stylesheet rules, feature-by-feature status, and what is deliberately out of
scope. Read it before changing anything.

Per-feature specifications live in [`.claude/specs/`](.claude/specs/), one file
per feature, and each is the source of truth for its own feature.

---

## License

MIT — see [LICENSE](LICENSE).
