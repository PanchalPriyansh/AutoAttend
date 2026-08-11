# Spec: Authentication

## Overview

This feature makes AutoAttend's three roles real. It adds credential-based login backed by the existing `users` collection, issues JWTs via **Flask-JWT-Extended**, and enforces role-based authorization on the Flask backend. On successful login the React app redirects the user to their role-specific portal (`/admin`, `/faculty`, `/student`), and the portal routes stop being publicly reachable placeholders. It also provides the bootstrap path for the very first admin account, since AutoAttend has no public registration. Every later feature (Admin Portal, academic hierarchy CRUD, attendance, student dashboard) depends on the `@jwt_required` / role-guard primitives introduced here, so this spec deliberately builds the authorization plumbing — not just a login form.

## Depends on

- `01-project-foundation` — Flask app factory (`create_app`), env-driven `Config`, CORS setup, and the React routing shell (`App.jsx`, `routes/Login.jsx`, the three portal placeholders).
- `02-database-setup` — the `users` collection with its `$jsonSchema` validator (`name`, `email`, `password_hash`, `role` enum, `institute_id`, `is_active`, `created_at`, `updated_at`) and the unique index on `email`.

## APIs

All endpoints are under `/api/auth`. Tokens travel as **HttpOnly cookies**, not in the JSON body.

- `POST /api/auth/login` — validates `{ email, password }` against `users`; on success sets the access and refresh token cookies plus their CSRF cookies and returns the safe user profile — **public**
- `POST /api/auth/refresh` — exchanges a valid refresh token cookie for a fresh access token cookie — **requires a valid refresh token**
- `POST /api/auth/logout` — clears all JWT and CSRF cookies — **any authenticated role**
- `GET /api/auth/me` — returns the current user's safe profile (`id`, `name`, `email`, `role`, `institute_id`); used by the frontend on page load to restore the session — **any authenticated role**

**No registration endpoint is added, now or ever.** Account creation belongs to the Admin Portal spec; the first admin comes from the CLI command below.

Response shapes must stay consistent with the existing `/api/health` style — JSON objects with meaningful HTTP status codes:

- `200` — success
- `400` — malformed/missing fields
- `401` — bad credentials, missing/expired/invalid token
- `403` — authenticated but wrong role
- `422` — malformed token (Flask-JWT-Extended's default for this case; handled explicitly so it surfaces as clean JSON)

The safe user profile shape returned by `login` and `me` must be identical, and must **never** include `password_hash`.

## Database changes

**No new collections and no schema changes.** This feature reads and writes the existing `users` collection defined in `02-database-setup`:

- `email` is stored and queried **lowercased and trimmed** — `schema.py` explicitly notes this is the Authentication feature's responsibility, and the unique index on `email` is case-sensitive, so normalization must happen in application code on every insert and every lookup.
- `password_hash` stores a Werkzeug-generated hash. Plaintext passwords are never stored, logged, or returned.
- `is_active: false` users must be rejected at login even with correct credentials.

No token blocklist collection is introduced — see "Rules for implementation" for the accepted trade-off.

## Frontend

- **Create:**
  - `frontend/src/api/client.js` — thin `fetch` wrapper: sends `credentials: 'include'` on every request, attaches the `X-CSRF-TOKEN` header (read from the JS-readable `csrf_access_token` cookie) on non-GET requests, and on a `401` transparently calls `POST /api/auth/refresh` **once** and retries the original request before giving up.
  - `frontend/src/context/AuthContext.jsx` — holds `{ user, loading }` plus `login()` / `logout()`; on mount calls `GET /api/auth/me` to restore an existing cookie session, and exposes a `useAuth()` hook.
  - `frontend/src/components/ProtectedRoute.jsx` — wrapper that redirects unauthenticated users to `/login` and users with the wrong role away from a portal they may not see; renders nothing (or a loading state) while `AuthContext` is still bootstrapping, so protected content never flashes before the check resolves.

- **Modify:**
  - `frontend/src/routes/Login.jsx` — replace the placeholder with a real email/password form: controlled inputs, submit handler calling `POST /api/auth/login`, an inline error message for failures, a disabled/pending state during submit, and a redirect to `/admin`, `/faculty`, or `/student` based on the returned `role`. An already-authenticated visitor is redirected straight to their portal.
  - `frontend/src/App.jsx` — wrap `/admin`, `/faculty`, and `/student` in `ProtectedRoute` with the required role.
  - `frontend/src/main.jsx` — wrap `<App />` in `<AuthProvider>` inside `<BrowserRouter>`.
  - `frontend/src/routes/AdminPortal.jsx`, `FacultyDashboard.jsx`, `StudentDashboard.jsx` — show the logged-in user's name and a working logout button. Keep them otherwise placeholder; their real content belongs to their own specs.
  - `frontend/.env.example` — document the API base URL variable if the client wrapper needs one (placeholder value only).

## Backend

- **Create:**
  - `backend/auth/__init__.py`
  - `backend/auth/passwords.py` — `hash_password(plaintext)` and `verify_password(plaintext, password_hash)` wrapping Werkzeug's `generate_password_hash` / `check_password_hash`. Isolated here so the hashing algorithm can change in one place.
  - `backend/auth/service.py` — the business logic, with no Flask request/response objects in its signatures:
    - `normalize_email(email)`
    - `authenticate_user(db, email, password)` → the user document or `None` (rejects unknown email, wrong password, and `is_active: false` identically)
    - `get_user_by_id(db, user_id)`
    - `create_user(db, name, email, password, role, institute_id=None)` → inserts a hashed, normalized, `is_active: true` user with real `datetime` `created_at`/`updated_at` values (per the `schema.py` note that dates must be BSON dates, not ISO strings), raising a clear error on duplicate email
    - `to_safe_profile(user)` → the public-facing dict, stripping `password_hash`
  - `backend/auth/decorators.py` — `role_required(*roles)`, layering a role check on top of `@jwt_required()` and returning `403` on mismatch. This is the primitive every later feature's protected route will use.
  - `backend/routes/auth.py` — the `auth_bp` blueprint with the four endpoints above. Handlers stay thin: parse and validate input, call `auth/service.py`, set/unset cookies, return JSON.

- **Modify:**
  - `backend/config.py` — add `JWT_SECRET_KEY` (from env, **no fallback default**), `JWT_TOKEN_LOCATION = ["cookies"]`, `JWT_COOKIE_CSRF_PROTECT = True`, `JWT_COOKIE_SAMESITE = "Lax"`, `JWT_COOKIE_SECURE` (defaults to `True`, may be `False` only in development), `JWT_ACCESS_TOKEN_EXPIRES` (~15 minutes), `JWT_REFRESH_TOKEN_EXPIRES` (~7 days), and `JWT_REFRESH_COOKIE_PATH = "/api/auth/refresh"` so the refresh cookie is not sent on ordinary API calls.
  - `backend/app.py` — initialize `JWTManager(app)`, register `auth_bp`, enable `supports_credentials=True` on the existing `CORS(...)` call (required for cookie auth; keep `origins=Config.CORS_ORIGINS` — do **not** widen it to `*`, which is invalid with credentials anyway), register JWT error handlers that return consistent JSON, and add a `flask create-admin` CLI command mirroring the existing `init-db` command — a thin `click` wrapper that prompts for name/email/password (password hidden, with confirmation) and delegates to `auth.service.create_user`.
  - `backend/requirements.txt` — add `Flask-JWT-Extended`.
  - `.env.example` — document `JWT_SECRET_KEY` and `JWT_COOKIE_SECURE` with placeholder values only.
  - `CLAUDE.md` — update the "Implemented vs stub features" table row for Authentication, and document the `flask create-admin` command alongside `flask init-db`.

## Files to change

- `backend/config.py`
- `backend/app.py`
- `backend/requirements.txt`
- `.env.example`
- `CLAUDE.md`
- `frontend/src/App.jsx`
- `frontend/src/main.jsx`
- `frontend/src/routes/Login.jsx`
- `frontend/src/routes/AdminPortal.jsx`
- `frontend/src/routes/FacultyDashboard.jsx`
- `frontend/src/routes/StudentDashboard.jsx`
- `frontend/.env.example`

## Files to create

- `backend/auth/__init__.py`
- `backend/auth/passwords.py`
- `backend/auth/service.py`
- `backend/auth/decorators.py`
- `backend/routes/auth.py`
- `frontend/src/api/client.js`
- `frontend/src/context/AuthContext.jsx`
- `frontend/src/components/ProtectedRoute.jsx`

## New dependencies

- Backend: **`Flask-JWT-Extended`** (pinned in the style of the existing entries, e.g. `Flask-JWT-Extended>=4.6,<5.0`).

Password hashing uses **Werkzeug**, which already ships as a Flask dependency — no `bcrypt`, `passlib`, or `flask-bcrypt` is to be added. No new frontend dependencies: `react-router-dom` is already installed and the native `fetch` API is sufficient.

## Rules for implementation

- **Authorization is enforced on the backend.** `ProtectedRoute` is a UX convenience only; every protected endpoint must independently verify the JWT and the role. A user editing localStorage or calling the API directly must gain nothing.
- **No public registration endpoint, ever.** The only account-creation path in this feature is the `flask create-admin` CLI command.
- **`JWT_SECRET_KEY` comes from the environment with no hardcoded fallback.** If it is missing, `create_app()` must fail loudly at startup rather than silently signing tokens with an empty or default key. Note that `backend/tests/test_no_secrets_and_scope.py` statically scans backend source for secret-like literal assignments — avoid patterns such as `SECRET_KEY = "…"` outside `os.environ` lookups.
- **Login failures return one generic message.** Unknown email, wrong password, and deactivated account must be indistinguishable in status code, body, and — as far as practical — timing, to prevent account enumeration.
- **`password_hash` never leaves the backend.** It must not appear in any API response, log line, or error message. Plaintext passwords must never be logged.
- **Normalize email on write and read.** Lowercase and trim in `auth/service.py` so the case-sensitive unique index on `email` behaves as intended.
- **Keep business logic out of route handlers**, per `CLAUDE.md`. `routes/auth.py` handles HTTP concerns; `auth/service.py` handles credential and user logic and holds no Flask request/response objects.
- **Import collection names from `database/schema.py`** (`USERS`) rather than hardcoding `"users"`.
- **`/api/health` and `flask init-db` behavior must be unchanged.** The app must still start and serve `/api/health` when MongoDB is unreachable; auth endpoints may fail in that state, but the process must not crash.
- **Accepted trade-off — no server-side revocation.** Logout clears the cookies, which ends the session for that browser. A token copied out beforehand stays valid until it expires; access-token lifetime is kept short (~15 min) to bound that window. If genuine server-side revocation is needed later, it warrants its own spec (a blocklist collection plus a `token_in_blocklist_loader`).
- **Rate limiting / brute-force lockout is out of scope** for this feature — it would require a new dependency. Do not add one here; note it as a follow-up.
- **Do not add password reset, "remember me", email verification, or SMTP** — notifications are a separate spec.
- **Do not touch attendance, face-recognition, or ML code**, and do not create their collections.
- **Do not seed real or realistic user data** in code or tests. Tests must use obviously-fake credentials and must never use a real password or production database.
- Preserve existing functionality: the existing `/api/health` tests and database tests must continue to pass unchanged.

## Definition of done

- [ ] `pip install -r backend/requirements.txt` installs `Flask-JWT-Extended`, and `python app.py` starts with `JWT_SECRET_KEY` set.
- [ ] Starting the app with `JWT_SECRET_KEY` missing fails immediately with a clear error instead of starting with an insecure default.
- [ ] `flask create-admin` creates an admin user in `users` with a hashed (never plaintext) password, a lowercased email, `role: "admin"`, `is_active: true`, and BSON `datetime` timestamps; running it again with the same email fails with a clear duplicate-email message rather than a raw stack trace.
- [ ] `POST /api/auth/login` with valid credentials returns `200`, the safe user profile (no `password_hash`), and sets HttpOnly access/refresh cookies plus the CSRF cookies.
- [ ] `POST /api/auth/login` with an unknown email, a wrong password, or an `is_active: false` account all return `401` with the same generic message and set no cookies.
- [ ] Login with mixed-case or padded email (`" Admin@College.edu "`) authenticates the same account as its normalized form.
- [ ] `GET /api/auth/me` returns the current user's profile with valid cookies, and `401` without them.
- [ ] `POST /api/auth/refresh` with a valid refresh cookie issues a new access cookie; with only an access cookie it is rejected.
- [ ] `POST /api/auth/logout` clears the auth cookies, after which `GET /api/auth/me` returns `401`.
- [ ] A state-changing request with valid cookies but a missing/incorrect `X-CSRF-TOKEN` header is rejected — CSRF protection is demonstrably active.
- [ ] An endpoint guarded by `role_required("admin")` returns `403` for an authenticated faculty or student token, and `200` for an admin — proving role enforcement lives in the backend.
- [ ] Logging in through the React UI as each role lands on `/admin`, `/faculty`, and `/student` respectively; a failed login shows an inline error and stays on the login page.
- [ ] Visiting `/admin`, `/faculty`, or `/student` while logged out redirects to `/login`, and a logged-in student visiting `/admin` does not see admin content.
- [ ] Reloading the page while logged in keeps the user signed in (session restored via `GET /api/auth/me`), and the logout button returns them to `/login`.
- [ ] No registration/sign-up endpoint or UI exists anywhere in the codebase.
- [ ] No secrets, real credentials, or plaintext passwords appear in any committed file, log output, or API response.
- [ ] `GET /api/health` and the existing `01`/`02` test suites still pass unchanged.
