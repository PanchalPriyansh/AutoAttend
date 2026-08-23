# Spec: Low-Attendance Notifications

## Overview

AutoAttend records attendance (`07`), lets faculty correct it (`08`), and lets a student look it up (`09`) — but every one of those requires somebody to go and look. The project's stated purpose is that "a student whose attendance is slipping usually finds out too late to fix it"; a dashboard only helps the student who already thought to check. This feature closes that loop by pushing the warning out: a sweep over recorded attendance finds every student below a configured percentage in a class they are enrolled in, and emails each one a plain-text summary of the classes they are short in.

It is a **threshold check against recorded attendance and nothing more** — no model, no training, no prediction, no `scikit-learn` (see `CLAUDE.md`, "Tech constraints"). The percentage compared against the bar is the same figure `09` already computes and shows the student; this feature adds the bar, the mail, and a record of what was sent.

This is the **last feature on the roadmap**. It is deliberately backend-only and CLI-triggered: no route, no blueprint, no React screen. An admin screen to set the threshold, preview recipients, and press Send is a separate optional `11` and is explicitly out of scope here.

## Depends on

- `01-project-foundation` — `create_app()`, `config.py` as the single env-driven configuration point, and the CLI-command pattern established by `flask init-db`.
- `02-database-setup` — `database/schema.py` as the sole declaration point for collection names, validators, and indexes; `init_database()` and `flask init-db`, which will create the one new collection this feature adds.
- `05-admin-user-management` — `class_enrollments` (and its `idx_student_id`) for who is still enrolled, `users` for each recipient's name/email/`is_active`, and `common/errors.py` for the shared exception vocabulary.
- `07-attendance-capture` — the `attendance_records` collection this aggregates, and its `idx_student_id_class_id` index.
- `09-student-attendance-dashboard` — the counting rule this feature must match exactly (the denominator is the student's own records, never the class's session count) and `academic/context.py::class_hierarchy_context` for the course/semester names that label a class in the email.

## APIs

**No API changes.** This feature registers no route, no blueprint, and no handler.

That is a deliberate constraint, not an omission. `backend/tests/test_app_factory.py` asserts that no registered route contains the fragment `"notification"`; that guard **stays passing after this feature lands** (only its explanatory comment changes, from "not implemented until its own feature spec" to "deliberately CLI-only"). If a later spec adds an admin trigger, that spec owns removing the guard.

The only new entry point is a CLI command:

```bash
flask notify-low-attendance            # send
flask notify-low-attendance --dry-run  # report who would be mailed; send nothing, write nothing
```

Exit codes follow `init-db` and `create-admin`: `0` on success, non-zero with a clear message and no raw traceback on failure.

## Database changes

**One new collection: `attendance_notifications`.** No existing collection, field, or index is modified.

```js
{
  student_id:    ObjectId,   // required
  class_id:      ObjectId,   // required — the cooldown key is (student_id, class_id)
  email:         String,     // required — where it actually went, at the time
  threshold:     Double,     // required — the bar in force for this send
  percentage:    Double,     // required — the figure that tripped it
  present_count: Int,        // required
  total_count:   Int,        // required
  sent_at:       Date        // required — a real BSON date, not an ISO string
}
```

Declared in `database/schema.py` as `ATTENDANCE_NOTIFICATIONS`, with a `$jsonSchema` validator in the same style as the existing nine, and appended to `COLLECTIONS` so `flask init-db` creates it.

**Index:** `{ student_id: 1, class_id: 1, sent_at: -1 }`, **non-unique**, named `idx_student_id_class_id_sent_at`. Non-unique on purpose — this is a history, not a latch. The cooldown is enforced by reading the most recent `sent_at` for the pair, not by a unique key, precisely so a student can be warned again later in the term.

**One email covers many classes, but produces one row per class.** A student short in three classes receives a single message and gets three `attendance_notifications` documents sharing one `sent_at`, because the cooldown is per class and so the record must be too.

**The message body is never stored** — only the figures that produced it. Nothing here holds a password, an SMTP host, or any biometric data.

## Frontend

- **Create:** nothing.
- **Modify:** nothing.

No React file is touched by this feature. Students already see their own percentages at `/student/attendance` (`09`); drawing the threshold line on that screen is a natural follow-up and is **not** in this spec.

## Backend

**Create — a new `backend/notifications/` package.** It is a sibling of `recognition/`, and isolated for the same reason: one concern, one place, and no other package imports it. Nothing outside this package imports `smtplib` or `email`, and this package imports nothing from `routes/`.

- `notifications/__init__.py`
- `notifications/errors.py` — `MailerNotConfiguredError` (SMTP settings missing or invalid) and `MailerSendError` (the transport refused). Bad input, missing target, and conflicting state already exist in `common/errors.py` and are reused, following `attendance/errors.py`'s precedent of declaring only what is genuinely new.
- `notifications/settings.py` — reads and validates `LOW_ATTENDANCE_THRESHOLD`, `NOTIFICATION_COOLDOWN_DAYS`, and the `SMTP_*` group off `Config` into a plain settings object. Raises `MailerNotConfiguredError` if a required SMTP value is absent or the threshold/cooldown is out of range. **This is the only module that reads SMTP configuration.**
- `notifications/messages.py` — builds the subject line and the plain-text body from a student's name and their below-threshold class list. Pure string construction: no database, no SMTP, no Flask. Keeping the wording here is what makes the body assertable in tests without a transport.
- `notifications/mailer.py` — the only `smtplib`/`email` importer. Builds an `email.message.EmailMessage` and hands it to a transport. **The transport is a parameter, not a hardcoded `smtplib.SMTP` call**, so tests inject a fake and no test ever opens a socket or holds a credential.
- `notifications/service.py` — the sweep. `find_low_attendance(db, threshold)` returns the below-threshold `(student, class, counts)` set; `notify_low_attendance(db, settings, mailer, *, dry_run=False)` applies the cooldown, sends, and records. No Flask objects in any signature, matching every other service module in the project.

The selection is one aggregation over `attendance_records` grouped by `(student_id, class_id)`, computing the percentage in the pipeline and `$match`ing below the threshold, so only candidate pairs come back — then narrowed in Python by enrollment, account state, and the minimum-lecture floor.

**Modify:**

- `backend/config.py` — add the `SMTP_*` group plus `LOW_ATTENDANCE_THRESHOLD` (default `75`) and `NOTIFICATION_COOLDOWN_DAYS` (default `7`), all via `os.environ.get`. `SMTP_HOST`, `SMTP_USERNAME`, `SMTP_PASSWORD`, and `SMTP_FROM_ADDRESS` get **no fallback value**, the way `JWT_SECRET_KEY` gets none — a missing credential must fail loudly at the point of use, never silently send from a default address.
- `backend/database/schema.py` — the collection name constant, validator, and index described above.
- `backend/app.py` — register the `notify-low-attendance` CLI command next to `init-db` and `create-admin`, with the same error handling: catch, log via `logger.exception`, print a clear one-line message to stderr, `raise SystemExit(1)`.

## Files to change

- `backend/config.py`
- `backend/database/schema.py`
- `backend/app.py`
- `backend/requirements.txt` — **comment only.** No package is added; a note records that the mailer is stdlib `smtplib`/`email` by design.
- `backend/tests/test_app_factory.py` — comment only, on `OUT_OF_SCOPE_ROUTE_FRAGMENTS`: `"notification"` now stays permanently rather than being "expected to be removed by the low-attendance email spec".
- `backend/tests/test_no_secrets_and_scope.py` — its module docstring still says notification libraries are "out of scope until their respective features"; update it, and extend the hardcoded-secret scan to cover the new package.
- `CLAUDE.md` — flip the Notifications row in "Implemented vs stub features" from "Follow current implementation status" to an implemented description with its deferred items.

## Files to create

- `backend/notifications/__init__.py`
- `backend/notifications/errors.py`
- `backend/notifications/settings.py`
- `backend/notifications/messages.py`
- `backend/notifications/mailer.py`
- `backend/notifications/service.py`
- `backend/notifications/reporting.py` — *added during code review.* Formats a finished sweep as lines for the CLI to echo. Not anticipated when this spec was written: the formatting first lived in `app.py`, and the quality reviewer noted that put ~45 lines of presentation logic into a module whose other two commands are 6–12 lines of thin wiring. Moving it here also makes the report text assertable without a `CliRunner`.
- `backend/tests/notifications_test_helpers.py`
- `backend/tests/test_notifications_settings.py`
- `backend/tests/test_notifications_messages.py`
- `backend/tests/test_notifications_mailer.py`
- `backend/tests/test_notifications_service.py`
- `backend/tests/test_notifications_reporting.py` — *added during code review*, covering the module above.

## New dependencies

**No new dependencies.** `smtplib` and `email.message` are in the Python standard library, which is exactly what `CLAUDE.md` requires ("SMTP/email libraries — Python's stdlib `smtplib`/`email` unless something is genuinely missing"). No mail framework, no queue, no scheduler, no task runner.

**Environment variables the user must add by hand.** A repository hook blocks Claude from reading or writing the environment files, so both the real one and the committed example must be updated manually. The keys, exactly as `config.py` will read them:

```
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM_ADDRESS=
SMTP_FROM_NAME=AutoAttend
SMTP_USE_TLS=true
LOW_ATTENDANCE_THRESHOLD=75
NOTIFICATION_COOLDOWN_DAYS=7
```

The committed example file must carry these keys with **empty or placeholder values only** — never a real host, address, or password.

## Rules for implementation

**Scope**

1. This is a threshold comparison. No model, no training, no prediction, no `scikit-learn`, no marks, no grades, no assessments.
2. No route, no blueprint, no React file. The CLI is the entire trigger surface.

**Who gets counted**

3. The percentage is **per class**, never an overall average. A student at 90% in two classes and 40% in a third is short in the third and must be warned about it; an overall average would hide exactly the case this feature exists to catch.
4. `percentage = present_count / total_count * 100`, where the denominator is **the student's own records in that class** — never the class's session count. Identical to `09`'s rule in `attendance/summary.py`; a student enrolled in week six must not be marked down for weeks one to five.
5. Only pairs where the student is **still enrolled** in the class are eligible. Records from a class the student has since been unenrolled from are ignored, matching `09`.
6. Only **active** students (`role == "student"`, `is_active == true`) with a non-empty email are notified. A deactivated account is skipped silently.
7. A class with fewer than `MIN_RECORDED_LECTURES` of the student's own records is skipped. Emailing "you are at 0%" after a single lecture is noise that trains people to ignore the mail. Defined as a documented module-level constant in `notifications/service.py` (value `5`) — **not** a fourth environment variable.
8. Membership is `percentage < threshold`, strictly. A student at exactly the threshold has met the bar and is not mailed.

**Sending**

9. **One email per student per run**, listing every class they are short in. Never one email per class.
10. Plain text only (`EmailMessage.set_content`). No HTML part, no attachment, no tracking pixel, no external link.
11. The body contains only **that student's own** data: their name, their classes, their own figures, and the threshold. It must never name another student, a class average, a roster, a rank, or a comparison.
12. The email must not state or imply an academic consequence, a prediction, or a risk score. It reports a recorded percentage against a configured bar.
13. **Send first, then record.** If the transport fails for a student, write nothing for them, log it, and continue to the next student — the next run retries. A duplicate email is a recoverable annoyance; a warning silently marked "sent" that never arrived is the failure this ordering exists to prevent.
14. One student's send failure must never abort the sweep. Aggregate the outcome and report counts at the end.

**Cooldown**

15. A `(student_id, class_id)` pair is skipped if `attendance_notifications` holds a row for it with `sent_at` within the last `NOTIFICATION_COOLDOWN_DAYS`. Enforced by reading the most recent row for the pair — not by a unique index.
16. If the cooldown filter removes every class for a student, that student is not emailed at all — no empty message.
17. `sent_at` is a timezone-aware `datetime` written as a real BSON date. Reattach naive values coming back from pymongo with `common/serializers.py::as_utc` before comparing them to a computed cutoff; this exact naive-vs-aware comparison has already produced one real `TypeError` in this project (`08`), and the in-memory test fakes do not catch it.

**Secrets and safety**

18. SMTP credentials are read from `Config` only, in `notifications/settings.py` only. They are never hardcoded, never logged, never printed by the CLI, never included in an error message, and never written to MongoDB.
19. Log and print recipient counts, not message bodies. A recipient address may appear in a `--dry-run` listing (an admin runs it deliberately at a terminal) but never in an error string.
20. `--dry-run` must open **no SMTP connection** and perform **no write**.

**Tests**

21. Tests use a **fake transport object** injected into the mailer. No test opens a socket, contacts a mail server, reads an environment file, or uses a real credential. No real email address appears in any test fixture.
22. Assert on the built `EmailMessage` — its recipient, subject, and body text — rather than on a send side effect. That is what makes rules 10–12 testable.
23. Include a leak test in the spirit of `09`'s: given two students both below threshold, assert that neither one's message contains the other's name, email, class, or figures.

**Standing project rules**

24. Follow the existing React + Flask + MongoDB architecture; keep frontend, backend, database, recognition, and notification responsibilities separated.
25. Keep academic data database-driven — no hardcoded class, course, or student.
26. Import no CV library on this path. Nothing in `notifications/` may reach `recognition/`, so the sweep must run on a machine where `dlib` and OpenCV are absent.
27. Preserve existing functionality: all 741 existing tests must still pass, unmodified except for the two comment-only test edits listed above.

## Definition of done

**Configuration**

- [ ] `config.py` exposes `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_ADDRESS`, `SMTP_FROM_NAME`, `SMTP_USE_TLS`, `LOW_ATTENDANCE_THRESHOLD`, and `NOTIFICATION_COOLDOWN_DAYS`, every one read from `os.environ`.
- [ ] `LOW_ATTENDANCE_THRESHOLD` defaults to `75` and `NOTIFICATION_COOLDOWN_DAYS` to `7` when unset; `SMTP_HOST`, `SMTP_USERNAME`, `SMTP_PASSWORD`, and `SMTP_FROM_ADDRESS` have no default.
- [ ] Missing or invalid SMTP configuration raises `MailerNotConfiguredError`, and the CLI reports it as a clear message with a non-zero exit and no traceback.
- [ ] A scan of the backend source finds no SMTP host, address, or password literal; `test_no_secrets_and_scope.py` passes with the new package in scope.

**Database**

- [ ] `database/schema.py` declares `ATTENDANCE_NOTIFICATIONS` with its validator and `idx_student_id_class_id_sent_at`, and it appears in `COLLECTIONS`.
- [ ] `flask init-db` creates the collection with its validator attached, and is still idempotent on a second run.
- [ ] A document missing any required field is rejected by the validator.

**Selection**

- [ ] A student below the threshold in one class is selected for that class only.
- [ ] A student at exactly the threshold is not selected.
- [ ] A student whose overall average clears the bar but who is below it in one class **is** selected for that class.
- [ ] A student is never selected for a class they are not enrolled in, even when `attendance_records` holds their rows for it.
- [ ] A deactivated student, a non-student user, and a student with no email are all skipped.
- [ ] A class with fewer than `MIN_RECORDED_LECTURES` of the student's own records is skipped even at 0%.
- [ ] The percentage for a given student and class equals the figure `GET /api/attendance/me` reports for the same pair.

**Email**

- [ ] A student short in three classes receives exactly one email listing all three.
- [ ] The message is plain text with no HTML part and no attachment.
- [ ] The body contains the student's own name, each short class with its percentage and present/total counts, and the threshold.
- [ ] The body contains no other student's name, email, figures, or class; no roster, average, rank, or comparison; and no predicted or risk-scored outcome.
- [ ] Given two below-threshold students, an automated test asserts neither message contains anything belonging to the other.

**Cooldown and recording**

- [ ] A successful send writes one `attendance_notifications` document per class covered, all sharing one `sent_at`.
- [ ] Running the command twice in a row sends zero emails on the second run.
- [ ] A pair whose last `sent_at` is older than the cooldown is mailed again.
- [ ] A transport failure for one student writes no `attendance_notifications` row for them, logs the failure, and does not stop the other students being mailed.
- [ ] A student whose every short class is inside the cooldown receives no email at all.

**CLI**

- [ ] `flask notify-low-attendance` is registered on the app's CLI and reports how many students were emailed and how many were skipped.
- [ ] `--dry-run` lists the would-be recipients, opens no SMTP connection, and writes no document — verified by a test asserting the fake transport was never invoked and the collection is unchanged.
- [ ] A database failure is reported clearly with a non-zero exit and no raw traceback.
- [ ] No SMTP password appears in any CLI output on any path, success or failure.

**Scope guards**

- [ ] `app.url_map` contains no route whose path includes `notification`, `prediction`, or `risk` — `test_app_factory.py` still passes.
- [ ] `backend/requirements.txt` gains no package.
- [ ] No file under `backend/notifications/` imports `face_recognition`, `numpy`, `cv2`, or anything from `recognition/`; the sweep runs on a machine with neither CV library installed.
- [ ] `smtplib` and `email` are imported nowhere outside `backend/notifications/mailer.py`.
- [ ] All 741 pre-existing tests still pass, plus the new suites.
- [ ] `CLAUDE.md`'s Notifications row reflects the shipped behavior and names what was deferred (admin trigger, admin-set threshold, showing the threshold on the student dashboard, per-institute thresholds, HTML mail).
