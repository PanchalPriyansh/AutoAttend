# Spec: Project Foundation

## Overview

This feature establishes the baseline project skeleton for AutoAttend before any real functionality (authentication, academic hierarchy, attendance, ML, notifications) is built. It sets up a Flask backend with a clean app/config/database-layer separation, a MongoDB connection driven entirely by environment variables, and a React frontend skeleton with routing placeholders for the Login page and the three role portals (Admin, Faculty, Student). No business logic, authentication, or academic data is implemented here — this is purely the scaffolding every later feature spec will build on top of.

## Depends on

Nothing — this is the first feature in the roadmap. All later specs (Authentication, Academic Hierarchy, Attendance, ML Prediction, Notifications) depend on this one.

## APIs

- `GET /api/health` — returns backend status and MongoDB connectivity state — no auth required (public)

No other API changes.

## Database changes

No collections or documents are introduced. This feature only establishes the MongoDB connection (via environment-configured URI) that later features will use to define collections.

## Frontend

- **Create:**
  - `frontend/` — new React app (Vite or Create React App; implementer's choice, document the chosen dev/build commands in `CLAUDE.md` once decided)
  - `frontend/src/App.jsx` — root component with route definitions
  - `frontend/src/routes/Login.jsx` — placeholder login page (no auth logic yet)
  - `frontend/src/routes/AdminPortal.jsx` — placeholder admin portal page
  - `frontend/src/routes/FacultyDashboard.jsx` — placeholder faculty dashboard page
  - `frontend/src/routes/StudentDashboard.jsx` — placeholder student dashboard page
  - `frontend/src/routes/NotFound.jsx` — 404 fallback page
  - `frontend/.env.example` — documents required frontend env vars (e.g. API base URL) with placeholder values only

- **Modify:** none — no existing frontend to modify.

## Backend

- **Create:**
  - `backend/app.py` — Flask app entry point / app factory
  - `backend/config.py` — loads configuration from environment variables (no hardcoded secrets)
  - `backend/database/__init__.py`
  - `backend/database/db.py` — MongoDB connection helper (reads `MONGODB_URI` from env)
  - `backend/routes/__init__.py`
  - `backend/routes/health.py` — `GET /api/health` blueprint
  - `backend/requirements.txt` — Flask, pymongo (or flask-pymongo), python-dotenv, flask-cors
  - `.env.example` (repo root or `backend/`) — documents required backend env vars (`MONGODB_URI`, `FLASK_ENV`, etc.) with placeholder values only

- **Modify:** none — no existing backend to modify.

## Files to change

None. There is no existing frontend/backend code in the repository yet.

## Files to create

- `backend/app.py`
- `backend/config.py`
- `backend/database/__init__.py`
- `backend/database/db.py`
- `backend/routes/__init__.py`
- `backend/routes/health.py`
- `backend/requirements.txt`
- `.env.example`
- `frontend/` app skeleton (exact generated files depend on chosen tooling)
- `frontend/src/App.jsx`
- `frontend/src/routes/Login.jsx`
- `frontend/src/routes/AdminPortal.jsx`
- `frontend/src/routes/FacultyDashboard.jsx`
- `frontend/src/routes/StudentDashboard.jsx`
- `frontend/src/routes/NotFound.jsx`
- `frontend/.env.example`

## New dependencies

- Backend: `Flask`, `pymongo` (or `Flask-PyMongo`), `python-dotenv`, `Flask-Cors`
- Frontend: `react-router-dom`

No other new dependencies — face recognition, ML, and notification libraries are out of scope until their respective features.

## Rules for implementation

- Follow the existing React + Flask + MongoDB architecture described in `CLAUDE.md`.
- Keep frontend and backend responsibilities separate; the frontend skeleton must not contain business or API logic beyond routing placeholders.
- No authentication, authorization, or session logic in this feature — `Login.jsx` and the portal pages are static placeholders only.
- No academic data, attendance, face recognition, ML, or notification code — those belong to later specs.
- MongoDB URI and any other configuration must come from environment variables (`.env`), never hardcoded; `.env.example` must contain placeholder values only, never real credentials.
- The Flask app must start and serve `/api/health` even if MongoDB is temporarily unreachable — connection failures should be handled gracefully (e.g. a clear error/status in the health response), not crash the process.
- Do not introduce unnecessary dependencies beyond what's listed above.
- Preserve `.gitignore`, `.env`, and any other existing repo files — do not commit real secrets.

## Definition of done

- [x] `python backend/app.py` (or documented equivalent) starts the Flask server without errors.
- [x] `GET /api/health` returns HTTP 200 with a JSON body indicating backend status and MongoDB connection status.
- [x] Stopping/blocking MongoDB access does not crash the Flask process; `/api/health` reflects the disconnected state instead.
- [x] `MONGODB_URI` and other config values are read from environment variables, not hardcoded anywhere in the backend code.
- [x] `.env.example` (backend and frontend) exists with placeholder values only, and `.env` remains gitignored.
- [x] `backend/requirements.txt` installs cleanly via `pip install -r requirements.txt`.
- [x] Frontend app starts via its documented dev command and renders without console errors. (Verified via successful `npm run build`/`npm run dev` and route-by-route HTTP checks; no browser extension was available this session for a live devtools console check — worth a quick manual confirmation.)
- [x] Frontend routing renders distinct placeholder pages for `/login` (or `/`), an admin route, a faculty route, and a student route, plus a 404 fallback for unknown routes.
- [x] No authentication, authorization, or academic-data logic exists anywhere in this feature's code.
- [x] No secrets or credentials are present in any committed file.
