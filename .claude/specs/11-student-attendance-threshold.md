# Spec: Student Attendance Threshold

## Overview

`10` closed the loop by *emailing* a student that they have fallen below the required attendance percentage, and that email tells them to check their dashboard. The dashboard has never displayed that percentage. A student sitting at 78% is fine today, is two missed lectures from not being fine, and has no way to see it — the number they are measured against exists only in `config.py` and in an email they receive after they have already dropped below it. `09` deliberately left this out ("A threshold is configuration that `11` introduces"), and `10` had to spell the bar out in words for exactly this reason.

This feature puts the bar on the screen the student already has. `LOW_ATTENDANCE_THRESHOLD` is added to the two existing student responses, drawn as a marker on the percentage bars at `/student/attendance`, stated per class as a met/below label in words, and turned into a "attend the next N lectures to reach it" figure derived from counts already on hand.

It is deliberately small and additive: **no new collection, no new field, no new index, no new endpoint, no new page, and no new dependency.** Every number it needs is already computed by `GET /api/attendance/me`; the only thing that was missing was the one number to compare them against.

What it must not become is a projection. `notifications/messages.py` states a recorded figure against a configured bar and stops, and every word this feature puts on screen follows the same discipline. There is no forecast of a final percentage, no risk score, no consequence, and no model — see the "Rules for implementation" section, which is the point of this spec more than the plumbing is.

## Depends on

- `03-authentication` — `role_required("student")`, the HttpOnly-cookie JWT, `requestJson` (`frontend/src/api/client.js`), `ProtectedRoute`.
- `09-student-attendance-dashboard` — everything this feature attaches to: `GET /api/attendance/me` and `GET /api/attendance/me/sessions`, `attendance/summary.py`, `attendance_percentage` and the two student serializers in `attendance/serializers.py`, `/student/attendance`, `AttendanceBar.jsx`, and the rule that no endpoint anywhere takes a student id.
- `10-low-attendance-notifications` — `Config.LOW_ATTENDANCE_THRESHOLD` (already parsed, already defaulted to `75`), and the wording discipline in `notifications/messages.py` that this feature's on-screen text must match. **This feature imports no code from `notifications/`** — see the rules below.

## APIs

**No new endpoints, no new routes, no signature change to any existing endpoint.** Two existing student responses gain fields:

- `GET /api/attendance/me` — **student** — gains a top-level `threshold`, and `meets_threshold` + `lectures_to_reach` on each entry in `classes`.
- `GET /api/attendance/me/sessions?class_id=…` — **student** — gains the same three fields for the one class it describes.

Query parameters, status codes, roles, and error contracts are all unchanged. No field is removed or renamed, so a client that ignores the new fields behaves exactly as before.

**Overview response** (`GET /api/attendance/me`), new fields marked:

```json
{
  "threshold": 75.0,
  "classes": [
    {
      "id": "...",
      "name": "A",
      "course": "Data Structures",
      "semester": "Semester 3",
      "department": "Computer Engineering",
      "institute": "...",
      "present_count": 20,
      "absent_count": 4,
      "total_count": 24,
      "percentage": 83.3,
      "meets_threshold": true,
      "lectures_to_reach": 0
    }
  ],
  "overall": {
    "present_count": 51,
    "absent_count": 9,
    "total_count": 60,
    "percentage": 85.0
  }
}
```

**Class detail response** (`GET /api/attendance/me/sessions`), new fields marked:

```json
{
  "class": { "id": "...", "name": "A", "course": "…", "semester": "…", "department": "…", "institute": "…" },
  "threshold": 75.0,
  "present_count": 20,
  "absent_count": 4,
  "total_count": 24,
  "percentage": 83.3,
  "meets_threshold": true,
  "lectures_to_reach": 0,
  "monthly": [ { "month": "2026-07", "present_count": 8, "absent_count": 1, "total_count": 9, "percentage": 88.9 } ],
  "sessions": [ { "date": "2026-08-14", "status": "present" } ]
}
```

Five things about these shapes are deliberate:

- **`overall` gains nothing.** `10` applies the bar **per class and never to an average**, because an average hides the class the student is actually failing: 85% overall can contain a class at 55%. Putting `meets_threshold` on the roll-up would state a pass/fail the institute never defined, and would contradict the only other place in the project that applies this number. The overall bar therefore also carries no marker.
- **`monthly` gains nothing.** A month is not the scope the bar is defined at. A student at 60% in July and 90% in August has met the requirement for the class; labelling July "below" would invent a judgement about a window nobody is assessed on.
- **`meets_threshold` is `true`/`false`/`null`, and `null` is not `false`.** It is `null` when `percentage` is `null` (nothing recorded yet) or when `threshold` is `null` (see below). A class whose first lecture has not been taken has not failed to meet the requirement — the same reason `09` returns `percentage: null` rather than `0`.
- **`lectures_to_reach` is `0` when the bar is met**, a positive integer when it is not, and `null` when there is nothing to compute from or when the answer is beyond the horizon of a semester (see `MAX_CATCH_UP_LECTURES` below). `0` and `null` are different answers and must not be collapsed.
- **`threshold` is the number as configured** (a float, `75.0` for the default), not pre-formatted text. Dropping a trailing `.0` for display is the client's job, exactly as `notifications/messages.py::format_percentage` does it for the email.

**`threshold` is `null` when the configured value is unusable.** `notifications/settings.py` refuses to run a sweep on a bad bar, and that is right — it is about to mail people. A dashboard is not: a misconfigured environment variable must not cost a student the attendance view they came for. So the read path degrades instead, returning `threshold: null` with `meets_threshold: null` and `lectures_to_reach: null` on every class, and the UI simply shows the attendance it always showed. This asymmetry is intentional and is not an inconsistency to be "fixed" by importing the notification validator.

## Database changes

**No database changes.** No new collection, no new field, no new index, no migration, no backfill, and no change to `database/schema.py` or `flask init-db`.

Nothing is written on either path. The threshold is configuration; `meets_threshold` and `lectures_to_reach` are derived at read time from counts that are themselves derived at read time. **No comparison, label, or catch-up figure is ever stored** — a stored "below threshold" flag is wrong the moment a faculty member corrects a session or an admin changes the bar, and nothing would know.

## Frontend

- **Create:**
  - `frontend/src/components/student/ThresholdNote.jsx` — the met/below line and the catch-up line for one class, rendered from `threshold`, `meets_threshold`, and `lectures_to_reach`. One component because the class rows and the detail panel say the same thing about the same class, and two copies would drift the first time the wording is adjusted — which, given the rules below, is the sentence in this feature least safe to have two versions of. It renders nothing at all when `threshold` is `null` or `meets_threshold` is `null`.

- **Modify:**
  - `frontend/src/components/student/AttendanceBar.jsx` — an optional `threshold` prop. When present and the bar is drawn, a marker line sits at that percentage across the track, and the bar's `aria-label` gains the requirement so the marker is not information available only to someone who can see it. When the prop is absent (the overall bar) the component renders exactly what it renders today.
  - `frontend/src/routes/student/AttendanceOverview.jsx` — pass `threshold` into each class row's `AttendanceBar` and render a `ThresholdNote` beside it; do the same in the detail panel; leave the overall bar untouched. The existing weakest-first ordering is unchanged — it already surfaces the class most in need of attention, and re-sorting by met/below would reorder the list every time the bar moved.
  - `frontend/src/utils/lecture.js` — add `formatPercentage(value)`, dropping a trailing `.0` so a bar of `75.0` reads as "75%" while a real `62.5` keeps its half. It belongs in the module whose docstring already says "small helpers shared by the attendance screens"; a second module for one function, or a second implementation of the same rounding, is not warranted.
  - `frontend/src/index.css` — the marker line and the two note states, built on the `--success` / `--warning` / `--muted` tokens that already exist. Note that `.attendance-bar-track` sets `overflow: hidden` and no positioning context: the marker needs `position: relative` on the track and must stay visible at 100%. Only the marker's computed offset belongs inline; a colour or dimension written into JSX is a value nothing else can reuse and nothing can theme.

The student UI must:

- show, for every class with attendance recorded, the percentage it has and the percentage it needs, in words and not only as a mark on a bar;
- say nothing about the requirement for a class with nothing recorded — it already reads "not taken yet", and a requirement stated against no data is noise;
- state the catch-up figure only when the class is below the bar and the figure exists;
- show the marker on per-class bars only, never on the overall bar;
- degrade to today's screen, with no marker, no note, and no error, when `threshold` is `null`;
- keep working without a full page reload, and keep every existing behaviour of `/student/attendance` intact.

## Backend

- **Create:**
  - `backend/attendance/threshold.py` — the bar and the arithmetic around it: `current_threshold()`, `meets_threshold(percentage, threshold)`, and `lectures_to_reach(present, total, threshold)`. Its own module rather than a few lines in `serializers.py` for two reasons. First, it is the one place that reads configuration on this path, and giving it a name makes "the dashboard's copy of the bar is read here and nowhere else" checkable. Second, `lectures_to_reach` is the only genuinely new logic in this feature and the only part with an edge case worth testing on its own; buried in a serializer it would only ever be tested through a JSON body.

    - `attendance_percentage(present, total)` **moves here from `attendance/serializers.py`**, unchanged. This was not anticipated when the spec was written and is not optional: `lectures_to_reach` has to apply the same rounding the student is shown, so it needs this function, while `serializers.py` needs `meets_threshold` and `lectures_to_reach` — a straight import cycle. Moving it is the fix rather than a deferred import, because the cycle is a symptom of the function being in the wrong module: it is a domain rule (how attendance is computed and rounded), not a serialization concern, and the proof is that `notifications/service.py` already imports it while serializing nothing. After the move the dependency runs one way, `serializers` → `threshold`, and there is still exactly one definition of the rounding rule. `_standing()` calls it through the new import and behaves identically.
    - `current_threshold()` reads `Config.LOW_ATTENDANCE_THRESHOLD` the way `database/db.py` reads `Config.MONGODB_URI` — importing `Config` directly, which is this project's idiom. It returns `None` rather than raising when the value is missing, a bool, non-numeric, or outside `(0, 100]`. It never raises. Its `MIN_THRESHOLD`/`MAX_THRESHOLD` bounds are declared locally rather than imported from `notifications/settings.py`, per the isolation rule below.
    - `meets_threshold(percentage, threshold)` returns `None` if either argument is `None`, otherwise `percentage >= threshold`. **Not strictly greater**: a student who has exactly met the bar has met it, which is the same comparison `10` makes (it mails only `percentage < threshold`).
    - `lectures_to_reach(present, total, threshold)` returns the smallest `n` such that attending the next `n` lectures would bring the recorded percentage to the bar — `0` when it is already met — found by walking `n` upward and applying `attendance_percentage` and `meets_threshold` at each step. Bounded by `MAX_CATCH_UP_LECTURES` (a module constant, `100`); beyond that it returns `None`.

      A loop rather than the closed form on purpose: the comparison has to be made against the *rounded* percentage, the same one the student is shown, so that "attend 3 more" and the label above it can never disagree. A closed-form solve on the unrounded ratio can be off by one in exactly the case where the student is closest to the bar and most likely to be counting. The cap exists because the answer is otherwise absurd at a bar of 100 — a student with a single absence would be told to attend twenty thousand lectures — and because an answer larger than a class holds in a semester is not an answer worth printing. It is a constant rather than a fourth environment variable for the same reason `MIN_RECORDED_LECTURES` is: it is a property of what makes a figure meaningful, not something a deployment tunes.

- **Modify:**
  - `backend/attendance/serializers.py` — `serialize_student_overview(entries, overall, threshold)` and `serialize_student_class_attendance(document, context, counts, monthly, lectures, threshold)` take the bar and add the three fields. `_standing()` is **not** changed: it is shared by the per-class, overall, and monthly figures, and the whole point of the shape above is that only one of those three scopes carries a judgement. A separate small helper (`_comparison`) builds the two comparison fields for the one scope that gets them. `attendance_percentage` leaves this module for `threshold.py` (above) and is imported back in.
  - `backend/notifications/service.py` — one import line, following `attendance_percentage` to its new module. The sweep's behaviour, its rounding, and the figures it mails are unchanged; this is the same function at a new path. `notifications/messages.py`'s docstring reference to that path is updated with it. **This does not weaken the isolation rule below** — the dependency runs `notifications` → `attendance`, which is the safe direction and predates this feature. It is the reverse that would drag SMTP onto a request path.
  - `backend/routes/attendance.py` — the two `/attendance/me` handlers call `current_threshold()` and pass the result to their serializer. One call each, no branching, no logic: the handlers stay as thin as they are today.
  - `CLAUDE.md` — mark this feature implemented in the "Implemented vs stub features" table, remove "students cannot see the attendance threshold" from the deferred lists under both *Student dashboard* and *Notifications*, and replace the "Next planned feature" section, since `11` was the last agreed step.

## Files to change

- `backend/attendance/serializers.py`
- `backend/routes/attendance.py`
- `CLAUDE.md`
- `frontend/src/components/student/AttendanceBar.jsx`
- `frontend/src/routes/student/AttendanceOverview.jsx`
- `frontend/src/utils/lecture.js`
- `frontend/src/index.css`
- `backend/tests/test_attendance_routes.py` — the two `/attendance/me` cases assert the new fields.
- `backend/tests/test_no_secrets_and_scope.py` — one added guard: `backend/attendance/` imports nothing from `notifications`.

## Files to create

- `backend/attendance/threshold.py`
- `backend/tests/test_attendance_threshold.py`
- `frontend/src/components/student/ThresholdNote.jsx`

## New dependencies

**No new dependencies.** `backend/requirements.txt` and `frontend/package.json` are untouched. This feature is one config read, one small loop, three JSON fields, and some CSS. Nothing here plots, models, predicts, or sends anything: no charting library (the marker is a positioned `div` on a bar that already exists), no `scikit-learn` or any ML framework, and no SMTP or mail library — `backend/tests/test_no_secrets_and_scope.py` already asserts the first of those and must keep passing.

## Rules for implementation

**Wording — the part of this feature most likely to go wrong**

- **The screen states a recorded figure against a configured bar, and stops.** Same discipline as `notifications/messages.py`, and for the same reasons: there is no model in this project, and attendance policy belongs to the institute, not to a dashboard that knows one number.
- **Never project a final percentage.** "You are on track for 71%", "at this rate you will finish below", "projected", "estimated", "expected", "forecast", "trending toward" — none of these may appear. The trend chart from `09` shows what happened month by month; it must not acquire a line for what will happen.
- **Never state a consequence.** No "you will be barred", "you may be detained", "you are at risk", "warning", "danger", "critical", "failing". The word for a student below the bar is *below*, and nothing follows it.
- **Never present any of this as a score, a rank, or a prediction.** No risk level, no grade, no comparison to other students, no class average, no percentile.
- **The catch-up figure is arithmetic on recorded numbers, and must be phrased as the condition it is** — "Attend the next 3 lectures to reach 75%" — not as a promise about the future and not as a statement of where the student will end up. It is the answer to "how many, from here?", which the student can already work out with a calculator; the feature only saves them the arithmetic.
- **Say the number in words next to the mark.** "Below the 75% requirement" is the statement; the marker on the bar illustrates it. A marker with no text is a line a student has to interpret.

**Scope and structure**

- **The bar is applied per class and never to an average.** No `meets_threshold` on `overall`, no marker on the overall bar, no threshold field on a `monthly` bucket, and no institute-wide or cross-class judgement anywhere.
- **`attendance/` must not import from `notifications/`.** The notification package is CLI-only by design — `test_app_factory.py` asserts no route contains `notification`, and that guard exists so nothing on a request path can send mail. Borrowing `load_sweep_settings`, `MIN_RECORDED_LECTURES`, or `format_percentage` from it would put that package on a request path through the import graph, which is the thing the guard is protecting. Both modules read the same `Config` value independently and that is correct.
- **`MIN_RECORDED_LECTURES` is deliberately not replicated here.** `10` skips a class with fewer than five recorded lectures because it is deciding whether to *interrupt someone with an email*, and 0% after one lecture is not worth an interruption. This screen is answering a student who came looking, and the honest answer to "how do I stand in this class?" is the recorded figure against the bar, with the counts (`1 of 2 lectures`) right beside it saying how little it currently rests on. The two rules diverge because the two decisions differ; that divergence is intentional, is not a bug, and must not be resolved by importing the constant.
- **Neither endpoint changes shape beyond adding fields.** No parameter is added, nothing is renamed, nothing is removed, no status code changes, and no client is required to send anything new.
- **The student is still always the JWT identity.** This feature adds no parameter to either endpoint, and above all no student id — `09`'s central design decision (no endpoint anywhere takes a student id) is untouched, and a threshold field is not a reason to revisit it.
- **Both endpoints stay `role_required("student")`.** A faculty or admin view of a named student's attendance is a different feature with a different authorization rule; it is not this one.
- **Keep business logic out of route handlers.** `routes/attendance.py` gains one call per handler and no branching. `attendance/threshold.py` holds no Flask objects and touches no database.
- **Nothing is written and nothing is stored.** No collection, no field, no cached comparison, no counter. If a `flask init-db` change seems necessary, something has been misunderstood.
- **Reuse before rewriting.** `attendance_percentage` already rounds to one decimal and returns `None` on an empty denominator; `AttendanceBar` already draws a bar and already handles the `null` case; `describeClass` already labels a class; `requestJson` already unwraps a response. A second rounding rule, a second bar component, or a second student API client is a defect.
- **No CV import on these paths.** Nothing here decodes an image or a video, and both endpoints must keep working on a server with neither `dlib` nor OpenCV built.
- **Never hardcode the threshold.** `75` may appear in `config.py` as the default and in test fixtures. It must not appear in a serializer, a route, a component, a stylesheet, or any user-facing string — every one of those reads the configured value.
- **Never hardcode academic data.** Unchanged from every prior spec: classes, enrollments, and hierarchy names come from MongoDB.
- **Accessibility: never convey met/below by colour alone.** The note carries the state in words; the marker is a shape with a label, not a red line; `AttendanceBar`'s `aria-label` names the requirement as well as the figure. This remains the one screen every student is guaranteed to open.

**Preserving what exists**

- **Preserve existing functionality.** `/api/health`, `flask init-db`, `flask create-admin`, `flask notify-low-attendance` (including `--dry-run`), every auth endpoint, the twenty academic endpoints, the ten user-management endpoints, the five face-enrolment endpoints, and all ten attendance endpoints must keep working, along with `/admin/academics`, `/admin/users`, `/admin/face-enrollment`, `/faculty/attendance`, `/faculty/attendance/history`, and `/student/attendance`.
- **The existing student screen must not regress.** The overall bar, the weakest-first ordering, the "not taken yet" state, the trend, the lecture strip, the lecture log, and the date filter all behave exactly as they do today.
- **Tests must not use real production credentials, a production database, real personal data, or any real biometric data.** The existing `attendance_test_helpers.py` fakes are extended narrowly where needed rather than duplicated; the threshold is set on the test config, never read from a real environment.

**Deferred deliberately, to be recorded in `CLAUDE.md` rather than solved here**

- The bar stays global rather than per-institute, per-course, or per-class, and stays in the environment rather than in MongoDB. An admin-set threshold is the larger optional spec `10` already named — it needs a blueprint, an admin route, and a collection, and it would undo the deliberately-kept `"notification"` route guard.
- Faculty and admins still cannot view a named student's attendance, and this feature does not move toward it.
- Faculty are not shown which of their students are below the bar; that is a class-scoped aggregation, not this screen.
- No export, no per-lecture explanation of why a student was marked absent, and no in-app notification of any kind.

## Definition of done

**Backend — the threshold module**

- [ ] `attendance/threshold.py` exists, imports `Config` directly, and imports nothing from `notifications/`, `flask`, `pymongo`, or any CV library.
- [ ] `current_threshold()` returns `75.0` with the default configuration.
- [ ] `current_threshold()` returns `None` — and does not raise — when the value is missing, `True`, a non-numeric string, `0`, negative, or greater than `100`.
- [ ] `meets_threshold()` returns `True` when the percentage exactly equals the bar, `False` below it, and `None` if either argument is `None`.
- [ ] `lectures_to_reach()` returns `0` for a class already at or above the bar, and for a class below it returns the smallest `n` for which `attendance_percentage(present + n, total + n)` meets the bar.
- [ ] `lectures_to_reach()` returns `None` when `total` is `0`, when the threshold is `None`, and when the answer would exceed `MAX_CATCH_UP_LECTURES` — including the bar-of-100-with-one-absence case, which must not produce a five-figure count.
- [ ] The figure it returns agrees with the label beside it in every case: there is no input for which a class is reported below the bar with `lectures_to_reach: 0`, or at the bar with a positive figure.

**Backend — the two endpoints**

- [ ] `GET /api/attendance/me` returns a top-level `threshold`, and every entry in `classes` carries `meets_threshold` and `lectures_to_reach`.
- [ ] `GET /api/attendance/me/sessions?class_id=…` returns the same three fields for the class it describes.
- [ ] `overall` carries no `threshold`, `meets_threshold`, or `lectures_to_reach`, and neither does any `monthly` bucket.
- [ ] A class with `percentage: null` returns `meets_threshold: null` and `lectures_to_reach: null` — never `false`, never `0`.
- [ ] With the threshold misconfigured, both endpoints still return `200` with the same attendance figures as before, `threshold: null`, and `meets_threshold`/`lectures_to_reach` `null` on every class.
- [ ] A class whose percentage exactly equals the bar reports `meets_threshold: true`, matching `flask notify-low-attendance`, which does not mail that student.
- [ ] Every field `09` returns is still returned, unchanged, on both endpoints; no parameter, status code, role, or error message has changed on either.
- [ ] Neither endpoint accepts a student id in any form, and neither writes anything.
- [ ] Faculty and admin still receive `403` from both; an unenrolled class is still `403`, a missing one still `404`.

**Frontend**

- [ ] `/student/attendance` shows, on every class with attendance recorded, a marker on its bar at the required percentage and a line stating in words whether the class is at or below it, naming the configured percentage.
- [ ] A class below the bar additionally shows the catch-up line when a figure exists; a class at or above it shows no catch-up line.
- [ ] A class with nothing recorded still reads "not taken yet" and shows no marker, no met/below line, and no catch-up line.
- [ ] The overall bar carries no marker and no met/below statement.
- [ ] With `threshold: null` the screen renders exactly as it did before this feature, with no error and no empty space where a note would be.
- [ ] A threshold of `75.0` reads as "75%" on screen; a threshold of `62.5` reads as "62.5%".
- [ ] Met and below are distinguishable without colour: each is stated in words, and the bar's accessible label names both the student's percentage and the requirement.
- [ ] The existing overall bar, weakest-first ordering, trend, lecture strip, lecture log, and date filter are unchanged.
- [ ] `npm run lint` and `npm run build` both pass.

**Scope guards**

- [ ] No file under `backend/attendance/` imports from `notifications/`, asserted by a test.
- [ ] `test_app_factory.py`'s assertion that no route contains `notification` still passes — this feature adds no route at all.
- [ ] `backend/requirements.txt` and `frontend/package.json` are byte-identical to their state before this feature.
- [ ] `test_no_secrets_and_scope.py` passes: no ML library, no SMTP configuration read outside `notifications/settings.py`, and no secret literal.
- [ ] No user-facing string in the backend or the frontend projects a percentage, predicts an outcome, scores a risk, or names a consequence.
- [ ] `flask init-db` is unchanged and still idempotent; `flask notify-low-attendance --dry-run` behaves exactly as before.
- [ ] The full `pytest` suite passes.
