# Spec: Change Password

## Overview

Every password in AutoAttend is currently set by somebody else. `flask create-admin` bootstraps the first one, `POST /api/users` sets one when an admin provisions an account, and `PUT /api/users/<id>/password` lets an admin reset any of them. There is no way for a person to change their own. That leaves exactly two outcomes, and both are bad: the password handed over when the account was created is never changed and keeps living in whatever channel delivered it, or it is changed by asking an admin — an admin who then knows it, and who had no business knowing it in the first place.

This feature adds one endpoint and one page. Any signed-in user proves their current password and sets a new one. That is the whole of it.

**This is the authenticated half, and only that half.** Forgot-password — the case where the user cannot prove the current password because they do not have it — is a separate later spec and is deliberately not started here. It needs a reset-token store (a new collection, expiry, single-use semantics), it needs an email path on a *request* thread when `10` and `11` deliberately made mail CLI-only and `test_app_factory.py` still asserts no route contains `notification`, and it needs the token-invalidation decision `05` recorded and deferred. None of those is required to let somebody who *is* signed in change their own password, and bundling them would make a small, self-contained slice wait on the largest open design question in the project.

Nothing about the stored document changes: a password changed here is byte-for-byte what an admin reset already writes, because it goes through the same `set_user_password`.

This is a backend vertical slice with a user-facing half, so it takes the full pipeline (`/test-feature`, then `/code-review-feature`) **and** the browser verification its UI requires — the same route `21` and `22` took.

## Depends on

- `01-project-foundation` — `create_app()`, blueprint registration, the `{ "error": "..." }` response contract.
- `03-authentication` — `auth_bp` and its existing `PyMongoError`/`RuntimeError` handler, `jwt_required`, `get_jwt_identity`, `auth/service.py::get_user_by_id`, `auth/passwords.py::verify_password`, and `frontend/src/api/client.js` (`apiFetch`, `requestJson`) with its cookie + CSRF handling.
- `05-admin-user-management` — `users/service.py::set_user_password`, `users/validators.py` (`MIN_PASSWORD_LENGTH`, `require_password`, `validate_password_length`), and the token-invalidation limitation recorded in that module's docstring, which this feature inherits rather than solves.
- `12-app-shell` — `AppShell`, the `.app-user` block, and `ProtectedRoute`, whose `role` prop is already optional so "any signed-in user" needs no change to it.
- `13-component-vocabulary` — `.card`, `.btn`, `.callout`, `.form-field`.
- `17-collapsible-nav` / `19-theme-toggle` — not used, but constraining: their measurements of the header are what this feature must not disturb. See rule 10.
- `18-spacing-scale` — any new spacing declaration composes `--space-*` or is deliberately off the scale.

## APIs

One endpoint, added to the existing `auth_bp` blueprint (`url_prefix="/api/auth"`).

- `POST /api/auth/password` — change your own password — **any signed-in role** (admin, faculty, student)

**It goes in `auth_bp`, not `users_bp`.** Every route in `routes/users.py` carries `@role_required("admin")`, and that uniformity is a property worth keeping — a single non-admin route inside it makes the blueprint's guarantee something you have to check line by line instead of read once. Credentials already live in `auth_bp` (login, refresh, logout, me), and this is a credential operation.

**No user id appears in the path or the body.** The account being changed is the one the JWT identifies, which is the same rule `09` set for the student attendance endpoints: an id that is never accepted cannot be tampered with. `PUT /api/users/me/password` was rejected for that reason as much as for the blueprint one — `me` in a path is an id-shaped slot that invites `me` to be replaced by something else.

**`POST` and not `PUT`, even though the admin reset beside it is a `PUT`.** The admin route replaces a field on a named resource and replaying it is harmless. This one consumes a proof: send it twice and the second call fails, because `current_password` is no longer current. That is an action, not an idempotent replacement.

### Request

```json
{ "current_password": "...", "new_password": "..." }
```

**There is no `confirm_password` field, and the backend must never accept one.** Confirmation exists to catch a typo in a control the user cannot read back, which is a property of the form, not of the account. The client compares the two boxes and refuses to submit; the server is told the answer once.

### Response

`200`:

```json
{ "message": "Password changed" }
```

Nothing else. Not the profile, not `updated_at`, not the user id — the caller already has all of it from `/api/auth/me`, and a write endpoint that returns a document invites the client to start trusting it as a read.

### Status codes

| Code | When |
|---|---|
| `200` | Changed. |
| `400` | `new_password` missing, not a string, or shorter than `MIN_PASSWORD_LENGTH`; `current_password` missing, not a string, or empty; `new_password` equal to `current_password`. |
| `401` | No valid access token, or the token identifies a user who no longer exists or has been deactivated. |
| `403` | `current_password` is wrong. |
| `500` | MongoDB unreachable — the blueprint's existing handler, unchanged. |

**`403` and not `401` for a wrong current password, and this is not a style preference.** `apiFetch` in `frontend/src/api/client.js` transparently refreshes and *retries* on any `401` whose path is not `/api/auth/login` or `/api/auth/refresh`. A `401` here would therefore submit the same wrong password a second time on every attempt — doubling the work of a brute-force attempt against a stolen session and making the audit story wrong. `403` is also the more accurate reading: the caller is authenticated, and is being refused an action they have not proved they may take.

## Database changes

No database changes. No new collection, no new field, no index. `set_user_password` already writes `password_hash` and `updated_at` on `users`, and this feature calls it rather than writing its own `$set`.

## Frontend

- **Create:**
  - `routes/Account.jsx` — the `/account` page, for all three roles. Renders the signed-in user's name, email, and role as read-only facts, then the change-password form.
  - `api/account.js` — `changePassword({ currentPassword, newPassword })`, on the existing `requestJson` client.
  - `styles/account.css` — `.account-*` page hooks.
- **Modify:**
  - `App.jsx` — the `/account` route, wrapped in `<ProtectedRoute>` **with no `role` prop**, which already means "any signed-in user".
  - `components/layout/AppShell.jsx` — the user's name in `.app-user` becomes a `<Link>` to `/account`. It is the only new affordance in the shell.
  - `styles/shell.css` — the `.app-user span` rule and its comment. That comment currently says "No class of its own: AppShell renders a bare `<span>` here"; after this it is a link and that stops being true, so the rule and the comment both move to the new hook.
  - `index.css` — `@import './styles/account.css'`, after `shell.css` and with the other pages.

**`navigation.js` is deliberately not touched, and the reason is measured rather than aesthetic.** Both the header nav and all three landing-page card grids read `navigationFor(role)`, so adding `/account` there adds it to six surfaces at once. It would take the student nav from one item to two, which retires `.app-nav--single` — and `17` measured collapsing a one-item nav as **17px worse** for students, so that regression comes back. It would take the faculty nav from two items to three, making "Take Attendance / Attendance History / Account" the new widest link list, which invalidates the 413px collapse breakpoint that `17` derived from exactly that measurement. A password form does not earn a permanent slot in the primary navigation of every page for every role, and it certainly does not earn re-deriving the header at three roles by ten widths. The account lives behind the user's own name, beside Log out, which is where the identity-scoped controls already are — and `logout` is the precedent: it sits in `.app-user` and has never been in `navigation.js` either.

## Backend

- **Create:**
  - `auth/password_change.py` — `change_password(db, user_id, *, current_password, new_password)`. Loads the user, verifies the current password, refuses a no-op change, and delegates the write to `users/service.py::set_user_password`. No Flask objects in any signature.
- **Modify:**
  - `routes/auth.py` — the new route, plus `@auth_bp.errorhandler(ValidationError)` → `400`, `NotFoundError` → `404`, and the feature's own `403`. The blueprint currently registers only the database handler.
  - `users/validators.py` — add `require_existing_password(body, field_name="current_password")`: a required, untrimmed, non-empty string with **no length check**. See rule 4.

**Why a new module rather than `auth/service.py`: `users/service.py` already imports `auth/service.py`** (`create_user`, `normalize_email`, `DuplicateEmailError`), so an `auth/service.py` that imported `users/service.py` back would be a circular import at module load. `auth/password_change.py` imports `users.service` and nothing imports it back, so the cycle does not exist. The alternative — writing `password_hash` directly from `auth/service.py` — was rejected outright: two modules writing the same credential field is how they drift.

## Files to change

- `backend/routes/auth.py`
- `backend/users/validators.py`
- `frontend/src/App.jsx`
- `frontend/src/components/layout/AppShell.jsx`
- `frontend/src/index.css`
- `frontend/src/styles/shell.css`
- `CLAUDE.md` — the feature table and "Next planned feature"

## Files to create

- `backend/auth/password_change.py`
- `backend/tests/test_auth_password_change.py`
- `frontend/src/api/account.js`
- `frontend/src/routes/Account.jsx`
- `frontend/src/styles/account.css`

## New dependencies

No new dependencies, backend or frontend. Hashing and verification are `werkzeug.security` through `auth/passwords.py`, which the login path has used since `03`.

## Rules for implementation

1. **Any signed-in role, and the identity comes from the token.** `@jwt_required()`, never `@role_required(...)` — an admin, a faculty member, and a student have exactly equal standing here. The user id is `get_jwt_identity()` and is never read from the body, the query string, or the path.
2. **Re-check `is_active` against the database, do not trust the claim.** `role_required` and `jwt_required` only inspect the token, and `05` left a deactivated account holding a valid access token for up to `JWT_ACCESS_TOKEN_EXPIRES` (15 minutes). Load the user by identity and return `401` if they are missing or `is_active` is false — the same check `POST /api/auth/refresh` already performs, for the same reason. A deactivated account must not be able to set a new password and walk back in.
3. **The current password is verified with `verify_password`, the same function login uses.** Not a re-implementation, not a string comparison, not a re-hash-and-compare. One code path decides whether a password is correct in this project.
4. **`MIN_PASSWORD_LENGTH` applies to the new password only, never to the current one.** `require_password` gives the new one the identical policy `flask create-admin` and the admin reset enforce, so the three cannot disagree. The current one is validated as present and non-empty and nothing more: if the minimum is ever raised, applying it to `current_password` would lock out precisely the people whose password is now too short — the ones who most need this endpoint. It is also **not trimmed**, for the reason `require_password`'s own docstring gives: surrounding spaces are legitimate characters in a credential and silently altering one makes a correct password look wrong.
5. **A password never appears in a response, a log line, or an exception message.** Not the current one, not the new one, not their lengths, not a prefix. The existing validators already promise this; the new module and the new route must not break it. The route's log line, if any, records the acting user's id and nothing else — the pattern the rest of the project already follows.
6. **A no-op change is a `400`, not a silent success.** If the new password verifies against the stored hash, refuse it with "New password must be different from the current password". Writing a fresh hash of the same secret would report success for a change that did not happen.
7. **The write goes through `set_user_password`, unwrapped.** Same discipline as `22`'s rule 3: the bulk path did not re-implement a rule that already existed. The `$set` on `password_hash` and `updated_at` lives in one place, so a document written here is indistinguishable from one an admin reset wrote.
8. **The session continues, and the spec says plainly what that does and does not buy.** No cookies are cleared, no redirect to `/login`, and the user stays on the page they were on. Changing a password does **not** invalidate an access token already issued to another session: that token stays valid for up to 15 minutes, after which `POST /api/auth/refresh` re-checks the database. This is `05`'s recorded limitation, inherited unchanged and deliberately not solved here — closing the window needs a token blocklist or a token-version claim, which is its own feature. Forcing a re-login was considered and rejected because it would *feel* like it solved this while solving none of it.
9. **No rate limiting, and it is recorded as a deferral rather than overlooked.** The endpoint is a password oracle for a caller who already holds a valid session, so it lowers the cost of confirming a password for someone who has already stolen a token — it does not open a new door. The project has no rate-limiting infrastructure at all (`22` deferred the same thing), and adding it for one endpoint would be the wrong place to start. State it in the feature table.
10. **The header's measured widths must not move.** `19` established that the user block has no slack: a 105px control cost two gap reductions and moved three one-row thresholds. This feature adds **no element** to `.app-user` — the name that is already there becomes a link, with the same text at the same font size. The ellipsis behaviour (`overflow`/`text-overflow`/`white-space`) must move with it onto the new hook, or a long name stops giving way to "Log out" and every measurement in `shell.css` is wrong. Prove it did not move rather than assume it: re-measure and confirm pixel-identical.
11. **The link must read as a link.** A visible focus ring, a hover affordance, and a colour that comes from `tokens.css` — never a raw colour. It is a `<Link>`, so it is keyboard reachable and announces as a link for free; do not add an `aria-label` that replaces the visible name, which would break the accessible-name-contains-visible-label rule.
12. **Confirmation is the form's job.** The two new-password fields are compared client-side, the mismatch is reported inline before any request is made, and only one value is sent. All three inputs are `type="password"` with the right `autoComplete` tokens (`current-password`, `new-password`, `new-password`), `name` attributes, and real `<label>` elements — the form hygiene group 4b applied to every admin page.
13. **The frontend gets its designed, responsive UI in this cycle** — per CLAUDE.md, no build-now-restyle-later. `.account-*` are page hooks composing `.card`, `.btn`, `.callout`, and `.form-field`; they must not restyle a `components.css` primitive. Any new spacing declaration composes `--space-*` or is deliberately and visibly off the scale.
14. **Errors and success both announce.** The backend's own message is what the user reads — a wrong current password says so, in the page's error callout — and the success and error regions are `aria-live="polite"`, as every other page in this project does it.
15. **`test_app_factory.py`'s route guard still passes.** `prediction`, `risk`, and `notification` must remain absent from every registered route; `/api/auth/password` adds none of them. `test_no_secrets_and_scope.py` must still pass in full, including the `cv2`, `face_recognition`, and `reportlab` import-isolation classes — nothing here imports any of them.
16. **Preserve existing behaviour.** Login, refresh, logout, `/api/auth/me`, the admin reset at `PUT /api/users/<id>/password`, and `flask create-admin` are untouched and still work exactly as before. The nine existing signed-in pages render identically.

## Definition of done

**Backend — authorization**

1. An unauthenticated request returns `401` and nothing is written.
2. An admin, a faculty member, and a student can each change their own password, and the resulting document is identical in shape to one written by the admin reset.
3. A valid access token belonging to a deactivated account returns `401`, and the password is unchanged — proved by leaving the account inactive and confirming the old hash still verifies.
4. A valid token whose identity no longer exists in `users` returns `401`.
5. No request body field can change *which* account is written: a body carrying `user_id`, `email`, or `id` is ignored, and only the token's own account is affected.

**Backend — validation and outcomes**

6. A correct `current_password` with a valid `new_password` returns `200 {"message": "Password changed"}`, and the new password authenticates through `POST /api/auth/login` while the old one no longer does.
7. A wrong `current_password` returns **`403`**, not `401`, and the stored hash is unchanged.
8. A missing, blank, or non-string `current_password` returns `400`.
9. A `new_password` shorter than `MIN_PASSWORD_LENGTH` returns `400` naming the minimum, and a missing or non-string one returns `400`.
10. A `new_password` equal to the current password returns `400`, and `updated_at` is unchanged.
11. A `new_password` with leading or trailing spaces is stored as given, and logs in with those spaces intact — it is not trimmed.
12. A `confirm_password` field in the body is ignored entirely, and its presence or absence changes nothing.
13. No response body on any status code contains a password, a hash, a length, or a prefix of either; and no log record emitted during a wrong-password attempt contains the submitted value.
14. A MongoDB failure surfaces as the blueprint's existing `500` JSON error, not as a `403` or a `400`.
15. `auth/password_change.py` imports no Flask objects, and its tests exercise it with a database double and no app context.
16. `pytest` passes in full, including `test_app_factory.py`'s route guard and every check in `test_no_secrets_and_scope.py`.

**Frontend**

17. `/account` renders for all three roles and shows the signed-in user's name, email, and role; it is unreachable signed out, redirecting to `/login`.
18. The user's name in the header links to `/account` from every one of the nine signed-in pages, and the header is otherwise unchanged.
19. Submitting with mismatched new-password fields reports it inline and sends **no** request, verified in the network panel.
20. A successful change clears all three fields, shows a success callout announced through `aria-live`, and leaves the user signed in and on `/account` — a subsequent navigation and a page reload both still work without re-authenticating.
21. A wrong current password shows the backend's own message in the error callout, leaves the fields as typed, and — verified in the network panel — issues **exactly one** request, confirming the `403` did not trigger `apiFetch`'s refresh-and-retry.
22. Logging out and back in with the new password succeeds, and the old password is rejected at the login screen.
23. The form is keyboard operable end to end, every control has a visible focus ring, each input has a real `<label>` and the correct `autoComplete` token, and a password manager offers to update the saved credential.
24. `/account` is measured at 320/360/414/480/636/768/1024/1440, every control reaches its 44px touch target at coarse-pointer widths, and the numbers are written into `account.css`.
25. **The header is re-measured at those same widths for all three roles and is pixel-identical to `main`** — `17`'s 413px collapse breakpoint, `.app-nav--single` for students, and `19`'s one-row thresholds all still hold, with a long name still ellipsing rather than pushing "Log out". The measurements go into `shell.css` beside the rule that moved.
26. Both themes are checked: the link, the focus ring, the callouts, and the inputs are all legible in light and dark, and no raw colour was added outside `tokens.css`.
27. `npm run build` completes with no new warnings.

**Process**

28. `/test-feature 23-change-password` and `/code-review-feature 23-change-password` have both run, and approved findings are fixed.
29. `CLAUDE.md`'s feature table and "Next planned feature" are updated, including this feature's own deferrals: no forgot-password (its own later spec), no rate limiting, no invalidation of tokens already issued to other sessions, no password-history or complexity rules beyond the length minimum, no email notification that a password changed, and no admin-visible record that it happened.
