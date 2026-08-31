# Spec: Forgot Password

## Overview

`23-change-password` shipped half of password management: a user who *knows* their password can replace it. This is the other half, and the only one that matters to somebody locked out — a user who cannot sign in gets a one-time code by email and uses it to set a new password.

**The code resets the password; it does not sign anybody in.** That single decision is what keeps this feature small. The alternatives were considered and rejected: a code that mints a session adds a permanent second authentication path beside `POST /api/auth/login`, and an emailed link that grants a scoped "may set a password" token adds a half-authenticated state that every later feature has to reason about. Here the code buys exactly one action, once, and the user then logs in normally with the password they just chose. There is no separate link-based reset to build first — the OTP *is* the forgot-password feature.

Two things that used to block it are gone. `23` built `auth/password_change.py` and proved the write can delegate to `users/service.py::set_user_password` unwrapped, and `24` settled the token-invalidation question `05` recorded — resetting a forgotten password now ends every existing session on that account for free, which is the correct outcome for a user who may be recovering from a compromise rather than mere forgetfulness.

**This is the first mail this project sends from a request thread, and that is a deliberate departure.** `10-low-attendance-notifications` and `11` made mail CLI-only on purpose, and `tests/test_app_factory.py` still asserts that no registered route contains `notification`. That guard survives this feature untouched — these routes are `/api/auth/forgot-password` and `/api/auth/reset-password`, and low-attendance mail remains reachable only through `flask notify-low-attendance` — but the property the guard was *standing in for* ("nothing on a request path can send mail") no longer holds, and the guard's own comment has to say so. `smtplib` is synchronous: sending occupies the worker for the duration of an SMTP connection, handshake, and send. That is stated rather than engineered around; see rule 16.

**There is no rate limiting anywhere in this project**, and a 6-digit code is one in 10⁶. Three compensating controls carry that weight, and they are the security core of the feature: the stored code is hashed, expires, and is single-use; a per-code **attempts cap** kills it after five wrong guesses; and a per-account **send cooldown** stops the request endpoint being a mail bomb. None of them is optional and none is configurable — see rule 4.

The request endpoint answers **identically whether or not the address belongs to an account**, including when it is inside the cooldown and when the send itself fails. `authenticate_user` already burns a dummy hash so login cannot be used to enumerate accounts; a forgot-password endpoint that says "no such user" would hand back what login refuses to.

This is a backend vertical slice with a real user-facing half, so it takes the full pipeline (`/test-feature`, then `/code-review-feature`) *and* a browser verification pass, and it ships its designed, responsive UI in this same cycle.

## Depends on

- `03-authentication` — `auth_bp`, its error handlers, `auth/service.py::get_user_by_id` / `normalize_email`, `auth/passwords.py::hash_password` / `verify_password`, and the login endpoint's non-enumeration behaviour, which this feature must match.
- `05-admin-user-management` — `users/service.py::set_user_password`, the single place this project writes `password_hash`, and `users/validators.py::require_password` / `MIN_PASSWORD_LENGTH`.
- `10-low-attendance-notifications` — `notifications/mailer.py` (`SmtpTransport`, `build_message`, `is_sendable_address`) and `notifications/settings.py::load_smtp_settings`. Both are reused unchanged; this feature adds no second way to send mail and no second reader of SMTP configuration.
- `23-change-password` — `auth/password_change.py` as the shape to follow, and its `403`-not-`401` finding, which applies here for a different reason (see rule 8).
- `24-invalidate-tokens-on-password-change` — `users.token_version`. A reset inherits the session-invalidation behaviour with no code of its own.
- `20-init-db-index-reconcile` — `database/indexes.py`, whose planner compares `keys` and `unique` only. This is why there is no TTL index; see "Database changes".

## APIs

Two new endpoints, both on `auth_bp`, both **unauthenticated** — no `@jwt_required`, no `@role_required`. A user who cannot sign in cannot present a token, and all three roles have equal standing over their own credential (`23`'s rule, extended to the signed-out case).

- `POST /api/auth/forgot-password` — request a code — no role, no authentication.
  - Body: `{"email": "..."}`.
  - **Always** `200 {"message": "If that email is registered, a reset code has been sent."}` — for a real account, an unknown address, a deactivated account, and an address inside the send cooldown alike.
  - `400` only for a body that is not a well-formed request at all (missing `email`, wrong type, not address-shaped). A malformed *request* is a different fact from an address that does not resolve, and refusing it says nothing about who has an account.
- `POST /api/auth/reset-password` — spend a code and set a new password — no role, no authentication.
  - Body: `{"email": "...", "code": "123456", "new_password": "..."}`.
  - `200 {"message": "Password reset"}` — and **no cookies are set**. The user is not signed in; the UI sends them to `/login`.
  - `400 {"error": "That code is not valid or has expired."}` — one message for every failure of the code: none issued, wrong, expired, already used, attempts exhausted, or the account is gone or deactivated.
  - `400` with the field's own message for a `new_password` that fails `require_password` (the same `MIN_PASSWORD_LENGTH` the admin reset and `flask create-admin` enforce).

**`email` is required on the reset call, not just the code.** Without it the server would have to find *any* account holding a matching code, which turns 10⁶ into a birthday problem across the whole user base — with a thousand codes outstanding, a single guess is a thousand times more likely to hit. Requiring the address scopes each guess to one account, which is what makes the attempts cap mean what it says.

**No endpoint returns whether a code exists, how many attempts remain, or when one expires.** The expiry duration is stated in the email, to the person who received it, and nowhere else.

### Why the failure message is one message

Six distinct internal outcomes collapse to one string. "No code has been issued for this address" tells an attacker the address is real and unspent; "you have used all your attempts" tells them the account is under attack and worth returning to; "that code has expired" tells them the address is real. The user who genuinely mistyped a digit is served just as well by one sentence, and the UI's recovery path — request another code — is the same in every case.

## Database changes

One new collection, `password_reset_codes`, added to `database/schema.py` as `PASSWORD_RESET_CODES` with a `$jsonSchema` validator and indexes, exactly like every other collection.

- `user_id` — `objectId`, required. The account the code resets. **Never an email**, so an address corrected between issue and use cannot repoint a live code.
- `code_hash` — `string`, required. The code is stored the way a password is, through `auth/passwords.py::hash_password`, so a read of this collection does not hand over working reset codes.
- `expires_at` — `date`, required.
- `attempts` — `int`, required, starts at `0`, `$inc`-ed on each wrong guess.
- `consumed_at` — `date`, optional and absent until spent. Single use is enforced by an atomic conditional update on its absence, not by a read-then-write.
- `created_at` — `date`, required. What the send cooldown is read from.
- `email` — `string`, required. Where the code was actually sent, captured at send time, mirroring `attendance_notifications.email` and for the same reason: an address corrected later must not rewrite the history of where a code went.

**The code itself is never stored, never logged, and never returned in a response.** It exists in memory long enough to be hashed and put in one email body.

Indexes:

- `{user_id: 1}`, **unique**, `uniq_user_id` — one code row per account, enforced by the database rather than by convention. It serves both reads this feature makes (the outstanding code for an account, and the cooldown), and it is load-bearing for security: it is what refuses the insert a concurrent `/forgot-password` request falls through to, and therefore what makes the cooldown hold under concurrency. See rule 5.

**There is deliberately no TTL index**, and the reason is specific rather than laziness. `database/indexes.py`'s planner compares `keys` and `unique` and treats every other option as "carries options not declared" — so an index created with `expireAfterSeconds` would be dropped and rebuilt *without* its TTL by the next `flask init-db`, silently, on every run. Making TTL work means teaching `plan_indexes` a new dimension, which is `20`'s feature and not this one, and a TTL index that quietly stops being a TTL index is worse than none. Instead: **expiry is enforced in code on every read** (a code past `expires_at` is refused whether or not a row still exists), a new request **deletes** that user's previous codes, and a successful reset deletes the row it spent. What lingers is at most one hashed, expired, unusable row per user who asked for a code and never used it. Recorded as a deferral.

`users` is unchanged. No field is added, and `token_version` is bumped by `set_user_password` exactly as it already is.

## Frontend

- **Create:**
  - `routes/ForgotPassword.jsx` — the signed-out page at `/forgot-password`, in two stages within one component. Stage 1 takes the email. Stage 2 takes the code, the new password, and a confirmation, and submits all three in **one** request. Success replaces the form with a confirmation and a link to `/login`. Like `Login.jsx` it renders no `AppShell`, and like `Login.jsx` it redirects an already-signed-in user to their portal.
  - `api/passwordReset.js` — `requestPasswordReset({ email })` and `resetPassword({ email, code, newPassword })`. A separate module from `api/account.js`, which is explicitly the *signed-in* user's own account.
  - `styles/forgot-password.css` — `.fp-*`, this page's own hooks only.
- **Modify:**
  - `App.jsx` — the `/forgot-password` route, public, no `ProtectedRoute`.
  - `routes/Login.jsx` — a "Forgot password?" link, and a revision to `.auth-note`, which currently tells a user who cannot sign in to contact their administrator. That was the whole truth until now.
  - `styles/login.css` → **renamed** `styles/auth-screen.css`, and `index.css`'s manifest and comment updated with it. Every rule in that file is already `.auth-*` and already describes "the signed-out screen"; it is now rendered by two pages, which by this project's own rule (`portal-card.css`, and its cautionary note that three copies is where a fourth comes from) makes it shared chrome rather than one page's file. **No declaration changes value** — this is a rename plus the two comments that name login specifically. `#root:has(.auth-screen)` already keys off the screen class, so the new page inherits it.

**No stage of this flow is reachable from `navigation.js`**, which is untouched for the reason `23` recorded: a link there appears on six surfaces and moves a measured breakpoint. This page is reached from `/login` and from an email, by people who are signed out.

## Backend

- **Create:**
  - `auth/reset_codes.py` — **pure**: no Flask, no pymongo, no `config`, no I/O. Generates a code with `secrets`, holds the four constants, and expresses the questions that decide whether a code still resets an account — is it expired, is it consumed, are its attempts exhausted, is this account inside its send cooldown — as the **query filters** `usable_code_filter(user_id, now)` and `reissue_filter(user_id, now)` (the latter matching a row old enough to be replaced, so it can be the filter of an atomic upsert — see rule 5). Filters rather than Python predicates over a fetched document, and that is not a stylistic choice: pymongo hands back **naive** datetimes while a freshly built `now` carries a timezone, and comparing the two raises `TypeError` — a bug this project has already shipped once, which `notifications/service.py::cooldown_skips` fixed by letting MongoDB compare two BSON dates. The same fix applies here. It also makes the rule impossible to apply half-way: there is one filter, and a code failing any condition is simply not found, which is exactly the indistinguishability the single error message needs. Its own module for the reason `auth/tokens.py` and `recognition/filenames.py` are: this is the rule that decides whether six digits from an email can take over an account, and it should be readable and testable without a database.
  - `auth/password_reset.py` — orchestration, in the shape of `auth/password_change.py`: takes a `db`, reads and writes the collection, and delegates the password write to `set_user_password` unwrapped. No Flask objects in any signature. Two public functions: `issue_reset_code` (one atomic upsert; returns the code to send or `None`) and `reset_password_with_code`.
  - `notifications/reset_messages.py` — the wording of the reset email. Pure string construction, the same discipline `notifications/messages.py` keeps: no document reaches it, so it cannot leak a field off one.
- **Modify:**
  - `routes/auth.py` — the two routes. They own the HTTP contract and, critically, the always-`200` shape: the service layer may distinguish outcomes internally, and the route is what refuses to report them. It gains `from config import Config` (it has none today) because `load_smtp_settings` reads its argument with `getattr`, so `current_app.config` — a dict — cannot be passed. It also gains a `MailerNotConfiguredError` handler returning **503 with a generic body**: those messages name configuration variables by design, and naming one to an anonymous caller is not the same as naming it on an operator's terminal.
  - `auth/errors.py` — `InvalidResetCodeError`, mapping to `400`. The per-package pattern `23` followed for `IncorrectPasswordError`: a failed *proof* is not a malformed *request*, so it is not `ValidationError` even though both answer 400. Its docstring carries the why-400 (rule 6).
  - `database/schema.py` — `PASSWORD_RESET_CODES`, its validator, its index, and its entry in `COLLECTIONS`.
  - `tests/test_app_factory.py` — the `OUT_OF_SCOPE_ROUTE_FRAGMENTS` comment. The `notification` entry stays and still passes; its comment must stop implying that no route can send mail, and record that `25` put transactional mail on a request path while low-attendance mail stayed CLI-only.

Nothing in `notifications/mailer.py` or `notifications/settings.py` changes. The route calls `load_smtp_settings(Config)` — never `Config.SMTP_*` directly, which `test_no_secrets_and_scope.py` enforces — and sends through a `SmtpTransport` context manager, so `smtplib` stays imported in exactly one file.

## Files to change

- `backend/routes/auth.py`
- `backend/auth/errors.py`
- `backend/common/validators.py` — `require_bounded_string` (rule 7)
- `backend/database/schema.py`
- `backend/notifications/errors.py` — its docstring claims nothing in the package is reachable from a route, which this feature makes false
- `backend/tests/test_init_db.py` — hardcodes the collection count
- `backend/tests/test_app_factory.py`
- `backend/tests/test_schema.py`
- `backend/tests/test_auth_routes.py`
- `backend/tests/auth_test_helpers.py` — **read it before trusting it.** `24`'s load-bearing test change was to this file's fake, which modelled `$set` only and would have silently dropped every `$inc`. This feature needs `$inc` on `attempts`, `$setOnInsert`-free inserts, a conditional `find_one_and_update`, and `delete_many` — if the fake does not model them, the tests pass while the code does nothing.
- `frontend/src/App.jsx`
- `frontend/src/routes/Login.jsx`
- `frontend/src/index.css`
- `frontend/src/styles/login.css` → `frontend/src/styles/auth-screen.css` (rename)
- `CLAUDE.md` — the feature table, the stylesheet manifest listing, and "Next planned feature"

## Files to create

- `backend/auth/reset_codes.py`
- `backend/auth/password_reset.py`
- `backend/notifications/reset_messages.py`
- `backend/tests/test_auth_reset_codes.py`
- `backend/tests/test_auth_password_reset.py`
- `backend/tests/test_notifications_reset_messages.py`
- `frontend/src/routes/ForgotPassword.jsx`
- `frontend/src/api/passwordReset.js`
- `frontend/src/styles/forgot-password.css`

## New dependencies

No new dependencies. The code comes from the stdlib `secrets`, the hashing from `werkzeug.security` through `auth/passwords.py`, and the mail from the stdlib `smtplib`/`email` already confined to `notifications/mailer.py`. No Redis, no cache, no rate-limiting package, no token library.

## Rules for implementation

1. **The request endpoint is not an enumeration oracle, in any dimension.** Unknown address, deactivated account, active account, and an account inside its cooldown all produce the same status, the same body, and the same headers. This includes the failure paths: a `MailerSendError` on the actual send is caught, logged server-side, and still answered `200`. The one uniform exception is a *misconfigured server* — `load_smtp_settings` is called **before** the user is looked up, so a deployment with no SMTP configured answers `503` to every address alike rather than only to addresses that exist.
2. **The code is generated with `secrets`, never `random`.** `secrets.randbelow(10 ** CODE_LENGTH)` zero-padded to `CODE_LENGTH`, so every value including `000123` is reachable and uniform. `random` is seeded predictably and is not for this.
3. **The code is stored hashed, through `auth/passwords.py`.** One code path in this project decides whether a submitted secret matches a stored one, and it is the one login uses. Do not compare plaintext, do not use a bare digest, and do not add a second hashing helper.
4. **The four numbers are constants in `auth/reset_codes.py`, not environment variables.** `CODE_LENGTH` (6), `CODE_TTL_MINUTES` (15), `MAX_ATTEMPTS` (5) and `RESEND_COOLDOWN_SECONDS` (60) are the entire compensating control for a project with no rate limiting; an operator raising `MAX_ATTEMPTS` in a `.env` is changing a security property from outside code review. They live beside the rule that reads them. This feature adds **no new configuration** and no new line to either env example.
5. **Issuing is one atomic write, and it happens BEFORE the send.** This reverses what this spec originally said, and the reversal is the point. The first draft followed `10-low-attendance-notifications`'s send-then-record rule: check the cooldown with a read, send, then write the row. `/code-review-feature` found that this makes the cooldown unenforceable — every concurrent request reads before any of them writes, so all of them pass and all of them send mail, with a window as wide as an SMTP round trip. The cooldown is the *only* thing stopping this endpoint being used as an anonymous mail bomb aimed at a third party's inbox, so a cooldown that concurrency walks past is not a control at all.

   So `issue_reset_code` claims the row and the cooldown slot in **one upserting `find_one_and_update`**, whose filter (`reissue_filter`) matches only a row older than the cooldown. No row → the upsert inserts. Row old enough → replaced in place. Row inside the cooldown → the filter misses, the upsert falls through to an insert, and the **unique index on `user_id`** refuses it with a `DuplicateKeyError` that is caught and reported as the same generic `None`. The database serialises the requests instead of the code hoping they arrive apart, and the unique index makes "exactly one live code per account" structural rather than a convention — several live codes would give an attacker that many independent `MAX_ATTEMPTS` budgets.

   **The price is real and is named rather than hidden**: a send that fails afterwards leaves a stored code the user never received, and has already replaced whatever code they were holding. The row is deliberately *not* rolled back, because deleting it would hand back the cooldown slot the request just claimed, which is the one thing that must not become cheap. Both costs are recovered by asking again a minute later, and neither is reachable by anyone but the account's owner — whereas being mail-bombed by a stranger is not recoverable at all.
6. **The attempts cap is charged atomically, in the same update that finds the code.** `find_one_and_update(usable_code_filter(...), {"$inc": {"attempts": 1}})`, never a `find_one` followed by a separate increment — concurrent guesses would each read `attempts` before the other's increment landed, so five would bound requests-in-flight rather than guesses, and that cap is the only reason a 6-digit code is safe. A **correct** code is charged an attempt too; it costs nothing, since the row is deleted on success, and it is the price of the count being exact rather than optimistic. A code that fails `usable_code_filter` is not charged at all, so an expired or already-spent code cannot be used to burn somebody else's remaining attempts.
7. **The submitted `code` is length-capped before it reaches a hash comparison.** `MAX_SUBMITTED_CODE_LENGTH` (64) via `require_bounded_string`. Not a format check — a wrong-length code must still fail as a wrong code, indistinguishably — but a ceiling on what an anonymous caller can make the server hash, which is free to send and not free to check.
8. **No route on this path returns `401`.** `frontend/src/api/client.js` transparently refreshes and *retries* any non-login, non-refresh 401 — so a 401 here would submit the same wrong code twice and burn two of five attempts per user action. That is `23`'s finding with sharper teeth, because here the retry consumes a limited resource. Every code failure is `400`.
9. **Single use is enforced by an atomic conditional update, not by a read-then-write.** Spending a code is a `find_one_and_update` filtered on `consumed_at` being absent; if it returns nothing, another request won and this one refuses. Two simultaneous submissions of the same valid code must not both reset the password.
10. **Consume before writing the password.** If the write then fails, the code is spent and the user needs a new one — the safe direction. The reverse ordering allows a replay.
11. **The password write delegates to `set_user_password`, unwrapped.** No re-hashing, no second `password_hash` writer, and no re-implementation of the `token_version` bump — which is what makes a reset end every existing session on the account, correctly and with no code of its own. `24`'s rule 1, and `23`'s rule 7, applied a third time.
12. **A successful reset sets no cookie and returns no profile.** It is not a login. `set_access_cookies` and `set_refresh_cookies` do not appear on this path, and the response body is one message.
13. **Expiry is checked in code on every read**, independently of whether the row still exists. There is no TTL index (see "Database changes"), so nothing may rely on expired rows having been swept.
14. **Nothing on this path is logged that could identify who is resetting what.** No code, no code hash, no email address, no user id in a log line. A send failure logs that a send failed and the transport's own text — which `notifications/mailer.py` already keeps free of the recipient — and nothing more. `23`'s rule 5, held to the same standard.
15. **The email carries the code in the body only, never in the subject.** A subject appears in a lock-screen preview and over the shoulder of whoever is next to the recipient. Plain text, no HTML part, and **no link** — there is no link-based reset, and a password email that trains users to click links is a phishing lesson. It states the expiry in minutes and says plainly that an unrequested code can be ignored because nothing has changed yet.
16. **The synchronous send is stated, not hidden.** `smtplib` holds the worker for the length of the connection and send. `SmtpTransport` already sets a 30-second socket timeout, which bounds it. No queue, no thread, no background worker is added — that is a different feature, and it is recorded as a deferral rather than half-built.
17. **`smtplib` and `email` stay imported only in `notifications/mailer.py`, and `SMTP_*` stays read only in `notifications/settings.py`.** Both are asserted by `test_no_secrets_and_scope.py` and both must still pass. No email address literal may appear in any new backend source file — the same test scans for one.
18. **`test_app_factory.py`'s route guard still passes unchanged.** `prediction`, `risk`, and `notification` remain absent from every registered route; `forgot-password` and `reset-password` contain none of them. Only the comment above the list changes, to record that the guard now means "low-attendance notifications are CLI-only" rather than "no route sends mail".
19. **The stylesheet rename carries every declaration across unchanged, and adds exactly one.** `login.css` → `auth-screen.css` is a rename plus rewritten comments, plus the single new `.auth-forgot` rule for the link that motivates the whole rename. Prove it by stripping comments from both files and diffing: 51 declarations before, the same 51 after, plus that one rule. Claiming byte-identity would be a promise a plain diff visibly breaks. A new page gets its own file, and neither page restyles a class the other owns.
20. **Preserve existing behaviour.** Login, refresh, logout, `/api/auth/me`, `POST /api/auth/password`, the admin reset, `flask create-admin`, `flask init-db`, and `flask notify-low-attendance` all behave exactly as before. `flask init-db` stays idempotent and re-runnable with the new collection declared.

## Definition of done

**Requesting a code**

1. `POST /api/auth/forgot-password` with a registered, active address returns `200`, and exactly one `password_reset_codes` document exists for that user, holding a `code_hash` that is not the code, an `expires_at` in the future, `attempts: 0`, and no `consumed_at`.
2. The same call with an unknown address, with a deactivated account's address, and with a registered address inside the cooldown all return byte-identical status, body, and headers to item 1 — and write no document and send no mail.
3. A second request outside the cooldown issues a new code **and deletes the previous one**; the earlier code is then refused at `/reset-password`.
4. A missing, non-string, or non-address-shaped `email` returns `400` naming the field, and writes nothing.
5. With SMTP unconfigured, every address — registered and unknown alike — receives the same `503`, and the response is produced without a user lookup having distinguished them.
6. A send that raises `MailerSendError` still returns the generic `200` and logs the failure without the recipient address. The row it already wrote **stays** — the code is stored but undelivered, and the cooldown slot stays claimed — because rolling it back would hand back the cooldown a request just spent. A further request inside the cooldown is still refused.
6b. **The cooldown holds under concurrency.** Two `issue_reset_code` calls for one account inside the window produce exactly one stored row and exactly one code to send; the second returns the same `None` an unknown address does. The unique index, not the application, is what refuses the second.
6c. **The attempts cap holds under concurrency.** The count is incremented in the same update that selects the code, so the number of guesses ever evaluated against one row cannot exceed `MAX_ATTEMPTS` regardless of how many requests are in flight.
6d. A `code` longer than `MAX_SUBMITTED_CODE_LENGTH` is refused `400` before any hash comparison happens.

**Resetting**

7. The code from the email resets the password: `200 {"message": "Password reset"}`, the user can immediately log in with the new password, and cannot log in with the old one.
8. The response sets **no** cookie of any kind — no access, no refresh, no CSRF — and carries no profile, no id, and no role.
9. Replaying the same code returns `400` with the single generic message, and the password is unchanged.
10. A wrong code returns that same `400`, increments `attempts` by exactly one, and leaves the code usable.
11. After `MAX_ATTEMPTS` wrong guesses the code is dead: the **correct** code is then refused with the same `400`, and recovery is only through a new request.
12. An expired code is refused even though no TTL index has swept it — proved by writing `expires_at` into the past and submitting the correct code.
13. A code issued for one account cannot reset another: submitting user A's code with user B's email returns the generic `400` and changes nothing.
14. A `new_password` shorter than `MIN_PASSWORD_LENGTH` returns `400` naming the rule, and **does not consume the code** — a length mistake must not cost the user their code.
15. A reset for an account deactivated between issue and use returns the generic `400` and writes no password.
16. Two simultaneous submissions of the same valid code result in exactly one password write; the loser receives the generic `400`.

**Session invalidation, inherited**

17. A reset increments `users.token_version`, and a session established before the reset fails its very next `POST /api/auth/refresh` with `401` — verified with no reset-specific invalidation code in the diff.
18. `password_hash` and `token_version` are never observed to move separately (`24`'s single-update property, re-checked through this path).

**The email**

19. The built message contains the code exactly once, in the body; the subject contains no code and no figure.
20. The message is plain text with no `multipart/alternative`, no HTML part, and no URL of any kind.
21. `notifications/reset_messages.py` is exercised with plain arguments — no document, no transport, no socket, no credential — and no Mongo document reaches it in production code either.

**Isolation and scope**

22. `auth/reset_codes.py` imports nothing from Flask, pymongo, `bson`, or `config`, and its tests run with no app context and no database double: `generate_code` is uniform over the whole space including zero-padded values, and both filters are asserted field by field — expiry, consumption, attempts, and the cooldown window — so every condition is pinned without a database.
22b. No datetime read from MongoDB is compared to a constructed one in Python anywhere on this path; every expiry and cooldown comparison happens inside a query. A test that only exercises the in-memory fake **cannot** catch a regression here (the fake stores aware datetimes), so this one is checked against a real MongoDB.
23. `smtplib` and `email` are imported nowhere outside `notifications/mailer.py`; `SMTP_*` is read nowhere outside `notifications/settings.py` and `config.py`; no email address literal appears in any backend source file. All three are the existing checks in `test_no_secrets_and_scope.py`, passing unmodified.
24. `test_app_factory.py`'s route guard passes with `notification` still in the list, and `flask notify-low-attendance` remains the only trigger for low-attendance mail.
25. Neither the plaintext code nor a `code_hash` appears in any response body, any log line, or any test fixture committed to the repo.

**Database**

26. `flask init-db` creates `password_reset_codes` with its validator and its one index, `--dry-run` writes nothing, and a second consecutive real run is a no-op reporting no rebuild.
27. The validator rejects a document missing `user_id`, `code_hash`, `expires_at`, `attempts`, `created_at`, or `email`, and accepts one without `consumed_at`.
28. No index on the collection carries `expireAfterSeconds`, so `plan_indexes` reports no drift on a second run.

**Frontend**

29. `/forgot-password` completes the whole flow in a browser against the real backend: request a code, read it from the mail transport, set a new password, land on a confirmation, and sign in with the new password.
30. A wrong code shows the backend's own message and, in the network panel, produces **exactly one** POST and no `/api/auth/refresh` — the `400`-not-`401` rule, verified the way `23` verified its own.
31. The mismatch between the new password and its confirmation is caught client-side before any request is made.
32. An already-signed-in user visiting `/forgot-password` is redirected to their portal, matching `Login.jsx`.
33. `/login` shows the "Forgot password?" link, and its closing note no longer implies that contacting an administrator is the only recovery path.
34. The page is measured at 320, 360, 413, 480, 768, 1024 and 1440 and the numbers are recorded in `forgot-password.css`; `autoattend-responsive-designer` has run on it. No horizontal overflow at any measured width, and the code input does not become a 6-character-wide box on a phone.
35. `styles/auth-screen.css` carries the same declarations `styles/login.css` did — 51 before, the same 51 after, proved by stripping comments from both and diffing — **plus exactly one new rule, `.auth-forgot`**, for the link the rename exists to serve. The comments that named login specifically are rewritten. Stating the addition here rather than claiming byte-identity means a future reader checking the claim with a plain diff finds what this says they will. `/login` renders pixel-identically to `main` at the seven widths above.
36. `npm run build` completes with no new warnings.

**Process**

37. `/test-feature 25-forgot-password` and `/code-review-feature 25-forgot-password` have both run, and approved findings are fixed.
38. `backend/tests/auth_test_helpers.py`'s fake was read before the suite was trusted, and models every operator this feature uses (`$inc`, conditional `find_one_and_update`, `delete_many`) — or the tests that would otherwise silently pass are written against a real database instead.
39. `pytest` passes in full.
40. `CLAUDE.md` records the feature and its deferrals: **no rate limiting** (the attempts cap, the cooldown, and the expiry are the whole of the control), no TTL sweep of expired rows and why, no queue or background send so the request thread holds an SMTP connection, the residual timing difference between a known and an unknown address, no notification to the user that their password was reset, no admin-visible record that a reset happened, and no lockout after repeated requests for one address.
