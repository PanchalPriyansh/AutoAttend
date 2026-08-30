# Spec: Invalidate Tokens on Password Change

## Overview

`23-change-password` let a signed-in user change their own password, and deferred one thing: a token already issued to another session keeps working. Its rule 8 said so plainly, inherited from `05-admin-user-management`, and bounded the exposure at "up to 15 minutes".

**That bound is wrong, and this feature exists because it is wrong.** `POST /api/auth/refresh` checks only `is_active` before minting a new access token — it never asks whether the credential the session was established with still exists. The refresh cookie lives `JWT_REFRESH_TOKEN_EXPIRES` = **7 days**. So a session holding stolen cookies survives a password change not for fifteen minutes but for a week, refreshing itself indefinitely, and the person who changed their password to end that session has not ended it. `/account`'s own copy tells the user "up to 15 minutes", which is currently false by three orders of magnitude.

This feature makes it true. A monotonic `token_version` on the user document is stamped into the refresh token when it is minted and re-checked when it is spent. Changing a password increments it, which strands every refresh token issued before the change; the acting session is handed a replacement in the same response, so the person who made the change stays signed in and everybody else is out. The remaining window is the access token's own lifetime, 15 minutes, which is what `23` and `05` already claimed and what will finally be the case.

The increment goes in `users/service.py::set_user_password` — the single place this project writes a password — so the admin reset at `PUT /api/users/<id>/password` gets the same behaviour for free and cannot drift from the self-service path. That is `23`'s rule 7 paying for itself.

This is a backend vertical slice, so it takes the full pipeline (`/test-feature`, then `/code-review-feature`). Its user-facing surface is one paragraph of copy on a page that already exists, so it needs a browser confirmation rather than a measurement pass.

**What this is not.** It is not forgot-password (`25`, and still the larger half). It is not a token blocklist: nothing is stored per token, no jti is recorded, and no lookup is added to any request path except the one that already reads the user. It is not a per-request session check — see rule 9.

## Depends on

- `03-authentication` — `auth_bp`, `create_access_token`/`create_refresh_token`, `set_refresh_cookies`, the `/refresh` route's existing `is_active` re-check, `auth/service.py::create_user`, and `JWT_REFRESH_TOKEN_EXPIRES` / `JWT_REFRESH_COOKIE_PATH` in `config.py`.
- `02-database-setup` — `database/schema.py`'s `USERS_VALIDATOR`, and the rule that `flask init-db` applies validators with `collMod` in place.
- `05-admin-user-management` — `users/service.py::set_user_password`, and the token-invalidation limitation recorded in that module's docstring. **This feature is the change that docstring says is out of scope there**; the docstring is rewritten, not deleted.
- `23-change-password` — `POST /api/auth/password`, `auth/password_change.py`, and its rule 8, whose deferral this closes. Its DoD item 20 ("leaves the user signed in") is a constraint here, not a suggestion.

## APIs

No new endpoints and no new request or response fields. Three existing routes change behaviour:

- `POST /api/auth/login` — unchanged contract; the refresh token it mints now carries a `token_version` claim.
- `POST /api/auth/refresh` — unchanged contract; now returns `401 {"error": "Authentication required"}` when the refresh token's `token_version` no longer matches the user's, in addition to the existing missing/inactive cases.
- `POST /api/auth/password` — unchanged `200 {"message": "Password changed"}` body; the response now additionally **sets a fresh refresh cookie** for the acting session.

**The 401 message is deliberately the generic one already used there.** A stale-credential refusal must be indistinguishable from a deactivated or deleted account, for the same reason `auth/errors.py::InactiveAccountError` gives: the refusal must not become a channel that tells a holder of stolen cookies *why* they were stopped.

**No route reports a version, and no response body carries one.** `token_version` is an internal counter; it appears in a signed cookie and in MongoDB and nowhere else. It is not in `to_safe_profile`, so `GET /api/auth/me` does not grow a field.

### Why the response to `POST /api/auth/password` sets a cookie

Incrementing the version strands *every* refresh token for that user, including the one belonging to the session making the request. Without a replacement, `23`'s promise that "you stay signed in on this device" would survive for exactly 15 minutes and then break — the user's next refresh would 401 and drop them at `/login` for the crime of changing their password. So the route mints a new refresh token carrying the new version and calls `set_refresh_cookies` on the 200.

**The access cookie is deliberately not re-issued.** Nothing checks a version on it, so replacing it would change what the user holds without changing what is true, and it would silently extend a session in a response whose subject is a credential. The one real side effect of re-issuing the refresh cookie is named rather than hidden: it resets that cookie's 7-day clock.

## Database changes

One optional field on the existing `users` collection.

- `users.token_version` — `int`, incremented every time `password_hash` is written. **Absent means `0`.**

`USERS_VALIDATOR` gains `"token_version": {"bsonType": "int"}` in `properties` and **does not gain it in `required`**. That is the whole of the migration story, and it is deliberate: MongoDB validates the *entire* document on update, so a required field would make every existing user document unwritable — `PUT /api/users/<id>` and `PUT /api/users/<id>/status` would start failing against the live database until somebody ran a backfill. Optional plus a `0` default in the read path means there is no backfill, no migration script, and no ordering dependency between deploying this code and running `flask init-db`.

`create_user` writes `token_version: 0` explicitly, so documents created from now on say what they are rather than relying on the default.

**No index.** The field is only ever read from a document already fetched by `_id`, and never queried on.

**Tokens issued before this feature are not invalidated by deploying it**, and that is the intended behaviour rather than an oversight: an absent claim reads as `0` and an absent field reads as `0`, so they match and existing sessions continue. The first password change after deployment takes the version to `1` and strands them, which is exactly the moment they are supposed to be stranded.

## Frontend

- **Create:** nothing.
- **Modify:**
  - `routes/Account.jsx` — the closing paragraph of the change-password panel. It currently reads "You stay signed in on this device. If you are signed in somewhere else, that session can keep working for up to 15 minutes." The second sentence describes behaviour that did not exist; after this feature the 15 minutes is real, and the copy should state the *outcome* (other sessions are signed out) rather than only the delay.

Nothing else in the frontend changes. `api/client.js` is untouched: `apiFetch` already re-reads the CSRF cookie per request, so a rotated refresh cookie needs no client-side handling, and a genuinely stale session already takes the existing path — refresh returns 401, the retry fails, and the component surfaces the error.

## Backend

- **Create:**
  - `auth/tokens.py` — the version rule as a pure module. No Flask, no pymongo, no config. Exports the claim name, `token_version_of(user)` (absent → `0`), `refresh_claims_for(user)`, and `is_token_current(claims, user)`. This is a five-line rule that decides whether a week-old cookie still authenticates somebody, which is precisely the kind of rule that should be readable and testable without a database — the same call `22` made for `recognition/filenames.py`.
- **Modify:**
  - `routes/auth.py` — `login` mints its refresh token with `additional_claims=refresh_claims_for(user)`; `refresh` adds the version check beside its existing `is_active` check; `change_own_password` re-issues the refresh cookie from the document `change_password` returns.
  - `auth/service.py` — `create_user` writes `token_version: 0`.
  - `auth/password_change.py` — no logic change; it already returns the updated user document, which is what the route needs to mint the replacement token. Its docstring's account of what a change does now needs to match what it now does.
  - `users/service.py` — `set_user_password` adds `"$inc": {"token_version": 1}` to its existing single `find_one_and_update`, and the module docstring's "Known limitation" paragraph is rewritten to describe what is now true.
  - `database/schema.py` — `USERS_VALIDATOR`.

**The increment shares the one update `set_user_password` already issues.** `$set` and `$inc` in the same document, so a password can never be written without its version moving, and there is no window where the new hash is live and the old tokens are too. Two separate writes would be that window.

## Files to change

- `backend/routes/auth.py`
- `backend/auth/service.py`
- `backend/auth/password_change.py`
- `backend/users/service.py`
- `backend/database/schema.py`
- `backend/tests/test_auth_routes.py`
- `backend/tests/test_auth_password_change.py`
- `backend/tests/test_user_routes.py`
- `backend/tests/test_schema.py`
- `frontend/src/routes/Account.jsx`
- `CLAUDE.md` — the feature table (`Authentication`, `Change password`, `Admin user management`) and "Next planned feature"

## Files to create

- `backend/auth/tokens.py`
- `backend/tests/test_auth_tokens.py`

## New dependencies

No new dependencies. `additional_claims` on `create_refresh_token` and reading them back with `get_jwt()` are both already-used Flask-JWT-Extended features — `login` passes `additional_claims={"role": ...}` to `create_access_token` today, and `auth/decorators.py` reads `get_jwt()` today. No blocklist store, no Redis, no cache.

## Rules for implementation

1. **The version is bumped in exactly one place: `set_user_password`.** Not in the route, not in `change_password`, not twice. It is the single function that writes `password_hash`, so putting the increment there is what guarantees the self-service change and the admin reset cannot behave differently — and what makes it impossible to add a third caller that forgets.
2. **`$set` and `$inc` go in the same update.** One round trip, one document version. A password whose hash has changed but whose version has not is the exact state this feature exists to prevent.
3. **Absent reads as zero, on both sides.** A user document with no `token_version` and a token with no `token_version` claim both mean `0`, and therefore match. Do not treat an absent claim as invalid: that would sign out every user in the database the moment this is deployed, to close a window that the very next password change closes properly.
4. **The comparison is exact equality of integers, not `>=` and not a timestamp.** A counter is used precisely because it is exact; a `password_changed_at` date would have to survive BSON millisecond truncation and JWT float seconds, and would be compared with an inequality that silently accepts a token minted in the same second as the change.
5. **The refresh route's refusal is indistinguishable from its other refusals.** Same status (`401`), same body (`{"error": "Authentication required"}`), same shape. A caller must not be able to tell "your password changed" from "your account was deactivated" from "your account is gone".
6. **`23`'s rule 8 is upheld for the acting session, and its DoD item 20 must still pass.** After a successful change the user is still signed in, still on `/account`, and a reload and a later navigation both work without re-authenticating — because the response replaced their refresh cookie, not because nothing was invalidated.
7. **`PUT /api/users/<id>/password` does not re-issue anything, and that is correct.** It acts on a named user who is normally not the caller, and there is no cookie of that user's to replace. An admin who points it at their own account is performing a reset on themselves and being signed out everywhere is the right outcome — including on the browser they did it from, within 15 minutes. Do not add a special case for it.
8. **Deactivation and logout are out of scope.** `refresh` already re-checks `is_active`, and `logout` clears cookies on one device. Neither touches `token_version`, and neither should: a version bump means "this credential was replaced", and stretching it to cover other things is how a counter stops meaning anything.
9. **No per-request session check is added, and the residual window is stated rather than hidden.** Verifying the version on *every* request would close the last 15 minutes, and it was considered and declined here: it adds a database read to every authenticated route in the project, which is a change to the auth model of every feature rather than a part of this one. It is recorded as a deferral in the feature table, along with what it would cost. The access token is therefore **not** given a version claim — nothing would read it, and a claim nobody checks is decoration that looks like a guarantee.
10. **`auth/tokens.py` imports nothing from Flask, pymongo, or `config`.** It takes a dict and a dict and returns a bool. Its tests run with no app context and no database double.
11. **No password, hash, or version appears in a log line or a response.** `POST /api/auth/password` still has no logging on its path at all (`23`'s rule 5), and this feature adds none. The refresh route's 401 logs nothing new.
12. **No new field is exposed.** `to_safe_profile` is untouched, so `GET /api/auth/me` and the login response keep exactly the shape they have.
13. **Preserve existing behaviour.** Login, logout, `/api/auth/me`, `@role_required`, the nine signed-in pages, `flask create-admin`, and `flask init-db` all work exactly as before. `flask init-db` remains idempotent and re-runnable, and the validator change applies through `collMod` without dropping or rebuilding anything.
14. **`test_app_factory.py`'s route guard still passes** — `prediction`, `risk`, and `notification` remain absent from every registered route; this adds no route at all. `test_no_secrets_and_scope.py` must still pass in full, including the `cv2`, `face_recognition`, and `reportlab` import-isolation classes.

## Definition of done

**The hole is closed**

1. A session that logs in, has its password changed by a *different* session, and then calls `POST /api/auth/refresh` receives `401` — proving the 7-day refresh token is dead the moment the password changes, which is the defect this feature exists to fix.
2. That same stale refresh token is still rejected on a second and third attempt, and no amount of retrying mints an access token.
3. The 401 body is byte-identical to the one a deactivated account receives from the same route, so the two cases are indistinguishable to the caller.
4. An admin reset via `PUT /api/users/<id>/password` strands the target user's refresh token identically — proved against the same assertion, without any code specific to that route.
5. `token_version` is `+1` after a self-service change and `+1` after an admin reset, and `password_hash` and `token_version` are observed to move in the same write (a document is never seen with a new hash and an old version).

**The acting session survives**

6. After a successful `POST /api/auth/password`, the response carries a `Set-Cookie` for the refresh cookie, scoped to `JWT_REFRESH_COOKIE_PATH`.
7. Using the cookie jar from that response, `POST /api/auth/refresh` succeeds and returns a working access token — the user who changed their password is not signed out.
8. The response body is still exactly `{"message": "Password changed"}` with no additional field, and no version appears anywhere in it.
9. A *second* session belonging to the same user, established before the change, fails to refresh — the two outcomes above hold simultaneously for the same account.

**Compatibility and defaults**

10. A user document with **no** `token_version` field authenticates, refreshes, and changes its password without error, and the change creates the field at `1`.
11. A refresh token minted **before** this feature (no `token_version` claim) still refreshes successfully against a user document with no field — deploying this signs nobody out.
12. That same pre-existing token is rejected after one password change on that account.
13. `create_user` writes `token_version: 0`, and a user created by `flask create-admin` and one created by `POST /api/users` are identical in this respect.
14. `flask init-db` applies the updated validator to a database holding users that lack the field, reports success, and afterwards `PUT /api/users/<id>` and `PUT /api/users/<id>/status` still succeed on one of those documents — proving the field was not made required.
15. `flask init-db --dry-run` still writes nothing, and a second consecutive real run is a no-op.

**Isolation and scope**

16. `auth/tokens.py` imports no Flask, pymongo, or `config` symbol, and `test_auth_tokens.py` exercises every branch with plain dicts, no app context, and no database double.
17. `POST /api/auth/logout` does not change `token_version`, and a session logged out on one device leaves another device's session working.
18. Deactivating and reactivating an account does not change `token_version`.
19. A wrong `current_password` returns `403`, changes no hash, **and changes no version** — a failed attempt must not strand anybody's sessions.
20. A rejected no-op change (`400`, new password equals current) likewise leaves `token_version` untouched.
21. `GET /api/auth/me` and the login response carry no `token_version`, and no response body in the project contains the string.

**Suite**

22. `pytest` passes in full, including `test_app_factory.py`'s route guard and every check in `test_no_secrets_and_scope.py`.
23. Existing `23-change-password` and `05-admin-user-management` tests pass unmodified except where they assert the old behaviour, and every such edit is a tightening rather than a relaxation.

**Frontend**

24. `/account` changes a password successfully, and the user remains signed in: a reload and a navigation to another page both work without re-authenticating (`23`'s DoD 20, re-verified).
25. A second browser session signed in as the same user before the change is signed out — verified end to end by letting its access token expire or by clearing only its access cookie, then observing the refresh 401 and the redirect to `/login`.
26. The `/account` copy states what actually happens, and its claim is checked against the implemented behaviour rather than against `23`'s text.
27. `npm run build` completes with no new warnings.

**Process**

28. `/test-feature 24-invalidate-tokens-on-password-change` and `/code-review-feature 24-invalidate-tokens-on-password-change` have both run, and approved findings are fixed.
29. `users/service.py`'s "Known limitation" docstring and `auth/errors.py::InactiveAccountError`'s docstring both describe the behaviour that now exists — neither may still say token invalidation is out of scope.
30. `CLAUDE.md`'s feature table and "Next planned feature" are updated, including this feature's own deferrals: no per-request session verification (the last 15 minutes stays open, and the cost of closing it is recorded), no per-device session list or "sign out everywhere" control, no blocklist for a stolen *access* token, no notification that other sessions were ended, and no record of when a password last changed.
