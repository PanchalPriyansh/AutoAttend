---
description: Improves the design and responsiveness of one AutoAttend page. Pass the page name e.g. /frontend-maker student dashboard
argument-hint: "Page name e.g. student dashboard, login, faculty attendance"
allowed-tools: Read, Edit, Write, Grep, Glob, Bash(git diff), Bash(git status), Bash(npx vite build), Bash(npm run dev), Task
---

Improve the frontend of one AutoAttend page: visual design first, then responsiveness, then an accessibility audit.

Page requested: `$ARGUMENTS`

## Step 1 - Resolve the page

If no argument was given, stop and say:

"Please name a page. Usage: /frontend-maker <page> e.g. /frontend-maker student dashboard"

Match `$ARGUMENTS` case-insensitively against this table. It is the whole list of pages that exist.

| Page | Route | Entry file |
|---|---|---|
| login / landing | `/login` | `frontend/src/routes/Login.jsx` |
| admin portal | `/admin` | `frontend/src/routes/AdminPortal.jsx` |
| academic hierarchy | `/admin/academics` | `frontend/src/routes/admin/AcademicHierarchy.jsx` |
| user management | `/admin/users` | `frontend/src/routes/admin/UserManagement.jsx` |
| face enrollment | `/admin/face-enrollment` | `frontend/src/routes/admin/FaceEnrollment.jsx` |
| faculty dashboard | `/faculty` | `frontend/src/routes/FacultyDashboard.jsx` |
| attendance capture | `/faculty/attendance` | `frontend/src/routes/faculty/AttendanceCapture.jsx` |
| attendance history | `/faculty/attendance/history` | `frontend/src/routes/faculty/AttendanceHistory.jsx` |
| student dashboard | `/student` | `frontend/src/routes/StudentDashboard.jsx` |
| student attendance | `/student/attendance` | `frontend/src/routes/student/AttendanceOverview.jsx` |
| not found | `*` | `frontend/src/routes/NotFound.jsx` |

**"Landing page" means the login screen** - AutoAttend has no public marketing page and no public registration.

If the argument matches nothing, stop and list the table. Do not guess, and do not create a page that does not exist - this command restyles existing pages only.

If it matches more than one (e.g. "attendance"), ask which, and stop.

Then read the entry file and note every component it renders from `frontend/src/components/`. Those are in scope too.

## Step 2 - Design

Invoke the **autoattend-css-designer** agent with:

- The page name, route, entry file, and the components it renders
- An instruction to load the `frontend-design` skill first
- A reminder that styling is split across `frontend/src/styles/` with `index.css` as the `@import` manifest, that it is still one global namespace so page-scoped selectors are preferred, that any shared-rule change must be reported with the pages it affects, and that the page's rules should be carved out of `scaffolding.css` into `styles/<page>.css`
- A reminder that colours come from the design tokens only, and that behaviour must not change

Wait for it to finish.

## Step 3 - Show the user, and stop

Report what the designer changed: the specific problems it found, what it did, any shared rules touched and which other pages those affect, any token added, and anything it deliberately left alone.

Then ask:

"Do you want to keep this design and continue to the responsive pass?"

**Wait for an explicit answer. Do not continue on your own.**

If the user wants changes, send them back to the same agent rather than starting a new one, so it keeps its context.

If the user rejects the design outright, offer to revert with `git checkout -- <files>` and stop.

## Step 4 - Responsiveness

Once the user has approved, invoke the **autoattend-responsive-designer** agent with:

- The page name, route, and files - **the page is named, so tell it explicitly to skip its significance gate**; the design work just approved is the significant change
- An instruction to verify in a real browser at 1440 / 1024 / 768 / 480 / 360 if Chrome tools are available, and to say plainly if they are not

Wait for it to finish.

## Step 5 - Accessibility and guidelines audit

Load the `web-design-guidelines` skill and run it over the files changed in steps 2 and 4.

That skill fetches its rules from a URL at runtime. If the fetch fails, say so and skip this step rather than inventing rules.

Report its findings as findings. **Do not apply them yet** - the user decides, the same way `/code-review-feature` works.

## Step 6 - Final report

```text
Frontend Report - <page>

Design changes
[what the css designer did]

Responsive changes
[what the responsive designer did, and which widths were actually verified in a browser]

Guidelines audit
[findings, or why the audit was skipped]

Shared-rule changes affecting other pages
[every one, or "none"]

Suggested next steps
[audit findings worth fixing, ranked]
```

Then confirm `npx vite build` passes from `frontend/`.

## Rules

- Do not commit. The user commits.
- Do not touch backend code, tests, `frontend/src/api/`, or `frontend/src/context/`.
- Do not add dependencies.
- Do not change component behaviour - this command restyles, it does not refactor.
- Do not run both agents at once. Design must be settled before responsiveness is worked on.
- Do not skip step 3's approval gate.
- One page per invocation. If the user names several, do them one at a time, finishing each.
