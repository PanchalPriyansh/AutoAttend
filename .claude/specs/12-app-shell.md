# Spec: App Shell

## Overview

Specs `01`–`11` built every capability AutoAttend has, one screen at a time, and gave each screen just enough CSS to be usable. **That styling is scaffolding.** It is being replaced page by page through `/frontend-maker`, in small related groups — login, then the student screens, then the faculty screens, then the admin panel. This spec is not part of that work and does not do any of it.

What this spec builds is the one thing those groups all sit inside and would otherwise each re-invent: the frame. Today there is no header, no persistent navigation, and no logout that lives anywhere but at the bottom of whatever page you happen to be on. Ten pages repeat `<h1>`, `Welcome, {user?.name}`, and a bare `<button onClick={logout}>`. Six sub-pages hand-roll their own `← Back to …` link. Three landing pages write out their own copy of the route list. If the four design groups run without a frame, each one decides independently what a header is, and the last group inherits three incompatible answers.

So this is deliberately **structural and nearly styleless**. It moves markup, deletes repetition, and establishes the landmarks — `<header>`, `<nav>`, `<main>`, one `<h1>`, a skip link, `aria-current` — that the design passes will later style. The CSS it adds is the minimum to keep the frame legible, is marked as temporary in the file, and is expected to be replaced.

**It deliberately does not define a component vocabulary.** No `.btn`, no `.card`, no `.table`, no `.input`, no spacing or radius scale. Designing those before a single page has been properly designed is guessing; the patterns that genuinely repeat should be extracted after two or three groups are done and there is real evidence of what repeats. That extraction is a later spec, not this one.

`Login.jsx` and `NotFound.jsx` are reachable while signed out, render no shell, and are **not touched by this spec at all** — login is the first design group and should arrive at `/frontend-maker` untouched.

## Depends on

- `03-authentication` — `AuthContext` (`user`, `loading`, `logout`), `ProtectedRoute`, and the rule that `logout()` clears `user` while `ProtectedRoute` performs the redirect. The shell renders the logout control; it does not change what logging out does.
- `04`–`11` — the ten routed pages this shell wraps.
- `.claude/commands/frontend-maker.md` — the pipeline that consumes this. This spec adds a short note to `autoattend-css-designer` so a design pass knows the shell exists and does not build a second header inside a page.

## APIs

**No API changes.** No new or modified endpoint, request shape, response shape, query parameter, status code, role requirement, or error contract.

This feature is frontend-only. Nothing under `backend/` is touched.

## Database changes

**No database changes.** No collection, no field, no index, no migration, and no change to `database/schema.py` or `flask init-db`.

Navigation is derived from `user.role`, which the client already holds from `GET /api/auth/me`. This is a presentation decision only — hiding a link a role cannot use is a convenience, never a security control. See the authorization rules below.

## Frontend

- **Create:**

  - `frontend/src/components/layout/AppShell.jsx` — the frame every authenticated page renders as its root. In order: a skip link, a `<header>` carrying the product name (linking to the current role's home), the role's navigation, the signed-in person's name, and the logout control; then `<main id="main">` containing the page's single `<h1>` and its children. Props: `title` (string, required) and `children`. No `back` prop — persistent nav replaces the six hand-rolled back links. No `user` prop — it reads `useAuth()` itself, as every page already does.

    One component rather than parts each page assembles: the repeated block is the header *plus* the main landmark *plus* the `<h1>` *plus* the skip target, and those four have to stay in the right relationship for the page to be navigable. Split up, they drift out of it.

  - `frontend/src/components/layout/NavBar.jsx` — the navigation list for one role. Marks the current route with `aria-current="page"` and a class, using `NavLink` from `react-router-dom` (already a dependency; this is what it is for). Separate from `AppShell` because it holds the only real logic here — matching the current location — and because the responsive pass will need to change it independently.

  - `frontend/src/navigation.js` — the single definition of what each role can navigate to: for `admin`, `faculty`, and `student`, an ordered list of `{ to, label, description }`. `description` is the sentence the landing pages already show under each link; keeping it here means the header nav and the landing page cannot disagree about what a destination is called.

    A plain module rather than a hook or context: it is a constant, it depends on nothing, and it must not export a component (see the lint rule below).

- **Modify:**

  - `frontend/src/routes/AdminPortal.jsx`, `frontend/src/routes/FacultyDashboard.jsx`, `frontend/src/routes/StudentDashboard.jsx` — wrap in `AppShell`; drop the local `<h1>`, the `Welcome, …` line, and the local logout button, all now provided by the shell; render their destination list from `navigation.js` rather than writing it out. These three become near-identical, which is correct — they are the same page for three roles.

  - `frontend/src/routes/admin/AcademicHierarchy.jsx`, `frontend/src/routes/admin/UserManagement.jsx`, `frontend/src/routes/admin/FaceEnrollment.jsx`, `frontend/src/routes/faculty/AttendanceCapture.jsx`, `frontend/src/routes/faculty/AttendanceHistory.jsx`, `frontend/src/routes/student/AttendanceOverview.jsx` — wrap in `AppShell`; drop the local `<h1>` and the back-link `<nav>`. **Nothing else in these six files changes.** Their forms, tables, camera panels, attendance visuals, filters, loading and error states, and all of their logic are untouched. This is a wrapper swap and two deletions per file.

  - `frontend/src/index.css` — one `/* --- App shell (temporary styling) --- */` banner holding the minimum needed for the frame to be legible: the header row, the nav list and its current-item marker, the skip link, and the main container. Built on the existing tokens. Commented as scaffolding that the per-page design passes are expected to replace.

  - `.claude/agents/autoattend-css-designer.md` — a short note that `AppShell` provides the header, nav, `<h1>`, and main landmark, so a page pass styles the shell's classes rather than building a second header inside a page.

  - `CLAUDE.md` — record this in the "Implemented vs stub features" table, and replace "Next planned feature" with the four design groups.

- **Not touched:** `frontend/src/routes/Login.jsx`, `frontend/src/routes/NotFound.jsx`, everything under `frontend/src/components/` other than the new `layout/` directory, `frontend/src/api/`, `frontend/src/context/`, `frontend/src/hooks/`, `frontend/src/utils/`, and `frontend/src/App.jsx`.

## Backend

**No backend changes.** Nothing under `backend/` is created, modified, or deleted.

## Files to change

- `frontend/src/index.css`
- `frontend/src/routes/AdminPortal.jsx`
- `frontend/src/routes/FacultyDashboard.jsx`
- `frontend/src/routes/StudentDashboard.jsx`
- `frontend/src/routes/admin/AcademicHierarchy.jsx`
- `frontend/src/routes/admin/UserManagement.jsx`
- `frontend/src/routes/admin/FaceEnrollment.jsx`
- `frontend/src/routes/faculty/AttendanceCapture.jsx`
- `frontend/src/routes/faculty/AttendanceHistory.jsx`
- `frontend/src/routes/student/AttendanceOverview.jsx`
- `.claude/agents/autoattend-css-designer.md`
- `CLAUDE.md`

## Files to create

- `frontend/src/components/layout/AppShell.jsx`
- `frontend/src/components/layout/NavBar.jsx`
- `frontend/src/navigation.js`

## New dependencies

**No new dependencies.** `frontend/package.json` is byte-identical afterwards; `backend/requirements.txt` is untouched.

Specifically ruled out, because a shell is exactly where they get smuggled in: no CSS framework, no icon package, no font CDN or `@font-face` download, no animation library, no headless component library, and no router replacement. `NavLink` — already inside `react-router-dom` — is the only new import from an existing package.

The `frontend-design` skill mentions an icon set. **This project has none and this spec does not introduce one.** Nav items and buttons are labelled with words.

## Rules for implementation

**Structure, not design**

- **This is a markup and deletion change.** For the six sub-pages the diff is: one added import, a changed wrapper element, and two deletions. If a diff on one of those files touches a form, a table, a fetch, a piece of state, or an attendance visual, it has exceeded this spec.
- **Add no component vocabulary.** No `.btn`, `.card`, `.input`, `.table`, `.alert`, `.empty-state`, and no spacing or radius token. Those come out of a later extraction spec, once two or three design groups have shown what actually repeats. Inventing them here is the thing this spec was trimmed to avoid.
- **The CSS added here is temporary and must say so in the file.** It exists so the frame is legible between now and the design pass that replaces it. Keep it short.
- **Do not restyle anything that exists.** The `.hierarchy-*`, `.attendance-*`, `.trend-*`, `.lecture-*`, `.dialog*`, and `.face-capture-*` sections are left exactly as they are, including where the three landing pages stop borrowing `.hierarchy-items` and `.user-identity` — those classes stay for the admin screen that owns them.
- **`.visually-hidden` already exists — reuse it** for the skip link rather than writing a second one.
- **Restraint over tidying.** `AttendanceCapture.jsx` and `AttendanceHistory.jsx` are 400-line files in scope for four lines each. Cleaning them up is `/frontend-maker`'s job, per group, with an approval gate this spec does not have.

**Behaviour is unchanged**

- **No behaviour changes anywhere.** No new state, no changed props on an existing component, no altered API call, no reordered effect, no changed loading or error handling, no new route, and no change to `App.jsx`'s route table. Logging out still calls the same `logout()` and still relies on `ProtectedRoute` to redirect.
- **Every existing route still works and still renders what it renders**: `/login`, `/admin`, `/admin/academics`, `/admin/users`, `/admin/face-enrollment`, `/faculty`, `/faculty/attendance`, `/faculty/attendance/history`, `/student`, `/student/attendance`, and the `*` fallback.
- **The camera paths must not regress.** `useCamera`, `FaceCapture`, and `ClassroomCapture` keep their behaviour, and `.face-capture-live video`'s width constraint stays — without it the preview renders at native resolution and pushes the capture button off screen.
- **`npm run lint` gains no new warning.** The one pre-existing `react(only-export-components)` warning at `AuthContext.jsx:51` is the accepted baseline. This is why `navigation.js` exports only data and no component.

Note that a *visual* change to the ten wrapped pages is expected and fine — a header now exists above them. What must not change is what they do.

**Accessibility, which is most of the point of a shell**

- **One `<h1>` per page**, provided by the shell from `title`. No page renders a second.
- **Real landmarks**: `<header>`, `<nav>`, `<main id="main">`.
- **The skip link is the first focusable element**, is invisible until focused, becomes visible when focused, and moves focus to the main region.
- **The current page is marked `aria-current="page"`** and is distinguishable without colour — weight or an underline as well as hue.
- **Focus is never removed.** Every interactive element in the shell has a visible `:focus-visible` state using `--focus`.
- **Never convey meaning by colour alone** — the project-wide rule from `09`/`11`, applying to the frame as much as to the attendance bars.
- **Both themes.** Every rule added is checked in light and dark; the tokens already carry both.

**Authorization — a frame is not a guard**

- **Hiding a nav link is presentation, never security.** `navigation.js` filters what is *shown* and must never become what decides what is *allowed*. Every route keeps its `ProtectedRoute role="…"` wrapper in `App.jsx` unchanged, and every endpoint keeps its backend `@role_required`. A student who types `/admin/users` is stopped by the same two guards that stop them today.
- **Display nothing about the user beyond `name` and `role`.** No email in the header, no id, nothing biometric.
- **No secrets, and no hardcoded academic data.** `navigation.js` holds application routes and their labels — never an institute, department, semester, course, or class, all of which come from MongoDB.

**Deferred deliberately, to be recorded in `CLAUDE.md`**

- All per-page visual design — the four `/frontend-maker` groups: login; student dashboard + student attendance; faculty dashboard + capture + history; admin portal + academics + users + face enrollment.
- Extracting a shared component vocabulary, once those groups have shown what repeats.
- Responsive behaviour, including whether narrow screens need a collapsible nav. `autoattend-responsive-designer` owns that and runs per group; guessing a breakpoint here would pre-empt it.
- A manual dark/light toggle. Tokens follow `prefers-color-scheme` and that stays.
- Breadcrumbs, page tabs, a notification bell, and global search — none have a feature behind them.

## Definition of done

**The shell**

- [ ] `AppShell.jsx` renders, in order: skip link, `<header>` (brand, nav, user name, logout), then `<main id="main">` containing exactly one `<h1>` from `title` and then its children.
- [ ] The skip link is the first focusable element, is invisible until focused, visible when focused, and moves focus to the main region.
- [ ] The header shows the signed-in person's name and a logout control on all ten authenticated pages.
- [ ] Logout from any page ends the session and lands on `/login`, via the same `logout()` and the same `ProtectedRoute` redirect as today.
- [ ] The brand links to the signed-in role's home (`/admin`, `/faculty`, `/student`).
- [ ] Each of the ten pages renders exactly one `<h1>`, with the same wording it shows today.

**Navigation**

- [ ] `navigation.js` is the only place any role's link list is written; no route path or nav label is duplicated in a component.
- [ ] An admin sees exactly Academics, Users, Face Enrollment; a faculty member exactly Take Attendance, Attendance History; a student exactly My Attendance.
- [ ] The current nav item carries `aria-current="page"` and is distinguishable without relying on colour.
- [ ] The three landing pages render their destinations from `navigation.js` and no longer use `.hierarchy-items`, `.user-identity`, or `.user-name`.
- [ ] All six hand-rolled back links are gone, and every page they were on is still reachable and leavable via the header nav.
- [ ] A student navigating directly to `/admin/users` is still redirected, and `GET /api/users` still returns `403` for them — hiding the link changed neither guard.

**Scope**

- [ ] `Login.jsx`, `NotFound.jsx`, and `App.jsx` are byte-identical to their state before this feature.
- [ ] No `.btn`, `.card`, `.input`, `.table`, `.alert`, or `.empty-state` rule was added, and no spacing or radius token.
- [ ] New CSS sits under one banner marked temporary, and every existing selector other pages rely on is still present and unmodified.
- [ ] No rule anywhere in `index.css` contains a raw colour value outside the token block.
- [ ] `frontend/package.json` and `backend/requirements.txt` are byte-identical; nothing under `backend/` changed at all.

**No regressions**

- [ ] All eleven routes render, and every page's existing content, controls, loading state, error state, and empty state behave as before.
- [ ] `/student/attendance` behaves as today — overall bar, weakest-first ordering, threshold markers, `ThresholdNote`s, "not taken yet" state, trend, lecture strip, and date filter all intact.
- [ ] `/faculty/attendance` and `/admin/face-enrollment` still open the camera, still preview at a constrained width, and still capture.
- [ ] `npm run lint` reports no warning other than the pre-existing `AuthContext.jsx:51` one.
- [ ] `npm run build` passes.
- [ ] The full `pytest` suite passes.
