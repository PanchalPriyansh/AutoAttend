# Spec: Component Vocabulary

## Overview

Two of the four `/frontend-maker` groups have landed — login (`styles/login.css`) and student (`styles/student-dashboard.css`, `styles/student-attendance.css`) — and the same handful of components have now been written twice, independently, in near-identical form. The *card* (`--surface` + `1px --border` + `12px` radius) exists four times. The *secondary button* exists twice with the same accent-border hover and two different paddings. The *error callout* exists twice, differing only by a glyph. A *field* and a *status pill* are one page short of the same fate. Spacing has converged on 2/4/8/12/16/20/32 and radii on 8/12/999 without either scale being written down anywhere.

This spec extracts that repetition into one file of real primitives, `styles/components.css`, and migrates the two finished groups onto it. It is scheduled **between group 2 and group 3** on purpose: group 3 is three faculty pages, and if it runs first every one of those components gets a third copy, written by an agent reading two prior copies and guessing which is canonical.

`12-app-shell` deliberately refused to define this vocabulary, and that refusal was correct — designing `.btn` and `.card` before any page had been designed is guessing. It is not guessing now. Every primitive below is taken from at least two shipped implementations, and **no primitive and no variant is defined here without an existing user.** A `.table`, a `.modal`, an `.empty-state` and a spacing scale are all things this project will plausibly want; none of them has two real implementations to extract from, so none of them is in this spec.

This is a refactor. It changes what the CSS is made of, not what the finished pages look like — with three small, named, deliberate exceptions listed under "Rules for implementation".

## Depends on

- `12-app-shell` — `AppShell.jsx` and `NavBar.jsx`, and `styles/shell.css`, which is marked temporary in the file and holds one of the two secondary buttons being extracted.
- Group 1, login — `styles/login.css`, `Login.jsx`. Source of the card, the primary button, the error callout with its glyph, and the large field.
- Group 2, student — `styles/student-dashboard.css`, `styles/student-attendance.css`, and the pages/components that render them. Source of the second card, the second secondary button, the second error callout, the compact field, and the status pill.
- `styles/tokens.css` — every colour a primitive uses already exists there, in both palettes. This spec adds radii to that file and no colours.

## APIs

**No API changes.** No new or modified endpoint, request shape, response shape, query parameter, status code, role requirement, or error contract.

This feature is frontend-only, and CSS-and-`className`-only within that. No component gains state, props, an effect, or a fetch.

## Database changes

**No database changes.** No collection, field, index, migration, or change to `database/schema.py`.

## Frontend

- **Create:**

  - `frontend/src/styles/components.css` — the vocabulary. Five families, each extracted from existing rules, plus a header comment stating the rule that governs the file: a primitive is added here only when two designed pages already implement it, and a page never restyles a primitive to suit itself.

    | Primitive | Extracted from | Notes |
    |---|---|---|
    | `.card` | `.auth-card`, `.student-home-card`, `.sa-card`, `.sa-class` | `box-sizing`, `padding: 20px`, `background: var(--surface)`, `border: 1px solid var(--border)`, `border-radius: var(--radius-md)`. Padding is the one declaration users routinely override (login 32, `.sa-class` 16), and page files load after this one, so an override is a plain later rule and not a specificity fight. No `box-shadow` in the base — of the four cards only login's carries one at rest. |
    | `.btn`, `.btn--primary`, `.btn--secondary`, `.btn--lg` | `.auth-submit`, `.app-logout`, `.sa-toggle` | `.btn` is chrome and size with no fill: `font: inherit`, `font-size: 0.85rem`, `font-weight: 500`, `padding: 8px 14px`, `border: 1px solid`, `border-radius: var(--radius-sm)`, `cursor: pointer`, `touch-action: manipulation`, `-webkit-tap-highlight-color: transparent`, and a 120ms background/border transition. `--primary` is `--accent` filled with `--bg` text (7.8:1 light, 9.0:1 dark — the pairing login already verified). `--secondary` is bordered, and takes the fill the surface under it does not: `background: var(--surface)` on the page, with one `.card .btn--secondary { background: var(--bg); }` rule, which is the relationship the shell and the student page each arrived at independently. `--lg` is login's size only (`font-size: 1rem`, `padding: 12px 16px`); `1rem` is load-bearing there and is what stops iOS zooming the page on focus. `:disabled` is `opacity: 0.65` in the base. |
    | `.callout`, `.callout--error`, `.callout-mark` | `.auth-error`, `.sa-error`, `.auth-error-mark` | The two are already identical apart from the glyph, down to `overflow-wrap: anywhere` — which is not cosmetic and must survive: the message is server text, and without it an unbroken 90-character token put a horizontal scrollbar on the whole document at 360px. `.callout` carries the box, layout, and wrapping; `.callout--error` carries `--danger`/`--danger-weak`; `.callout-mark` is the optional round glyph. `--error` is the only variant, because it is the only one that exists. |
    | `.pill`, `.pill--success`, `.pill--warning` | `.threshold-standing`, `--met`, `--below` | `padding: 2px 8px`, `border: 1px solid`, `border-radius: var(--radius-pill)`, `font-size: 0.75rem`, `font-weight: 500`. Two variants only. The pill has one user today rather than two, and is included because the extraction is otherwise identical to the button and card cases and because it is the primitive faculty attendance history most obviously needs — but that also makes it the one to leave alone if it does not come out clean. |
    | `.field`, `.field--lg` | `.auth-field`, `.sa-field` | The column and its 6px gap, the label (`--text-h`, `0.8rem`, `500`), and the control chrome for `input` **and** `select` (`font: inherit`, `font-size: 0.9rem`, `padding: 8px 10px`, `background: var(--bg)`, `border: 1px solid var(--border)`, `border-radius: var(--radius-sm)`, `min-width: 0`, and the `--text-muted` hover border). `select` is included though neither designed page has one, because it is the same control chrome and every faculty and admin form is built from both — this is the one place the file may run one step ahead of its users. `--lg` is login's size, for the same iOS reason as `.btn--lg`. `.sa-field`'s `flex: 0 1 180px` is layout, stays on the page. |

- **Modify:**

  - `frontend/src/index.css` — one `@import './styles/components.css';` between `base.css` and `login.css`. Primitives must load before every page file, so a page can override one with a plain later rule.

  - `frontend/src/styles/tokens.css` — add `--radius-sm: 8px`, `--radius-md: 12px`, `--radius-pill: 999px` to `:root`, outside the theme blocks (radii do not change with the palette), and widen the file's header comment, which currently describes it as the palette. **No new colour.** **No spacing tokens** — see the rules below.

  - `frontend/src/styles/base.css` — one global `:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }`. The identical ring is currently written three times (`.auth-screen :focus-visible`, `.student-attendance :focus-visible`, and the grouped shell selector), and the fourth page group would write a fourth. This belongs in base rather than components because it is not tied to any primitive — it is the project-wide rule that focus is never removed.

  - `frontend/src/styles/login.css` — drop the rules now carried by `.card`, `.btn`/`--primary`/`--lg`, `.callout`/`--error`/`.callout-mark`, and `.field`/`--lg`, and the local focus ring. Keeps: the `#root:has(.auth-screen)` unpadding, `.auth-screen`'s full-height centring with its safe-area gutters, the card's `max-width: 400px`, `padding: 32px` and `box-shadow`, `.auth-brand`, `.auth-title`, `.auth-form`, `.auth-note`, and the in-flight `cursor: progress` on the submit button.

  - `frontend/src/styles/shell.css` — `.app-logout` becomes `.btn .btn--secondary` in the markup; its rule and its hover go, and its entry leaves the grouped focus selector. Everything else in this temporary file is untouched.

  - `frontend/src/styles/student-dashboard.css` — `.student-home-card` drops the surface/border/radius now in `.card` and keeps its padding override, `position: relative`, chevron `::after`, transitions, hover, pressed and focus handling.

  - `frontend/src/styles/student-attendance.css` — the largest reduction: `.sa-card`, `.sa-class`'s chrome, `.sa-toggle`, `.sa-error`, `.sa-field`, and `.threshold-standing*` lose what the primitives now carry. Every rule that is layout, data, or page-specific state stays exactly as it is — the grid columns, the 460px gauge cap, the 502px/693px/1160px breakpoints and all of their reasoning comments, `.sa-class.is-open`, `.sa-class.is-open .sa-toggle`, and the whole `.attendance-bar*` / `.trend*` / `.lecture-*` block.

  - `frontend/src/routes/Login.jsx`, `frontend/src/components/layout/AppShell.jsx`, `frontend/src/routes/StudentDashboard.jsx`, `frontend/src/routes/student/AttendanceOverview.jsx`, `frontend/src/components/student/ThresholdNote.jsx` — `className` changes only. Each element that becomes a primitive gets the primitive's classes, and keeps its page hook alongside where the page still styles it (`className="btn btn--secondary sa-toggle"`). **No other edit to these five files:** no changed JSX structure, no changed props, state, handler, effect, request, or conditional.

  - `.claude/agents/autoattend-css-designer.md` — replace the paragraph stating there is deliberately no component vocabulary with the vocabulary itself: what the five families are, that a page composes them and adds its own hook for page-specific bits, that a primitive is never restyled to suit one page, and that wanting a sixth primitive is a report note rather than permission to add one.

  - `CLAUDE.md` — the "Stylesheets" section gains `components.css` in the manifest listing and the rule that governs it; "Next planned feature" records the extraction as done and promotes group 3 (faculty) to next.

- **Not touched:** `frontend/src/styles/scaffolding.css`, `frontend/src/App.jsx`, `frontend/src/navigation.js`, `NavBar.jsx`, `NotFound.jsx`, `AttendanceBar.jsx`, `AttendanceTrend.jsx`, `LectureStrip.jsx`, everything under `frontend/src/api/`, `context/`, `hooks/`, `utils/`, and all eleven undesigned admin and faculty pages.

## Backend

**No backend changes.** Nothing under `backend/` is created, modified, or deleted.

## Files to change

- `frontend/src/index.css`
- `frontend/src/styles/tokens.css`
- `frontend/src/styles/base.css`
- `frontend/src/styles/login.css`
- `frontend/src/styles/shell.css`
- `frontend/src/styles/student-dashboard.css`
- `frontend/src/styles/student-attendance.css`
- `frontend/src/routes/Login.jsx`
- `frontend/src/routes/StudentDashboard.jsx`
- `frontend/src/routes/student/AttendanceOverview.jsx`
- `frontend/src/components/layout/AppShell.jsx`
- `frontend/src/components/student/ThresholdNote.jsx`
- `.claude/agents/autoattend-css-designer.md`
- `CLAUDE.md`

## Files to create

- `frontend/src/styles/components.css`

## New dependencies

**No new dependencies.** `frontend/package.json` and `backend/requirements.txt` are byte-identical afterwards.

Specifically ruled out, because an extraction spec is exactly where they get smuggled in: no CSS framework or reset, no utility-class library, no CSS-in-JS, no CSS modules (the manifest is a single global namespace by design), no PostCSS plugin, no icon package, and no headless component library. Nothing here needs a build step that does not already exist.

## Rules for implementation

**Evidence, not invention**

- **No primitive without two existing implementations**, and **no variant without a user.** The callout gets `--error` and no `--success`/`--warning`/`--info`. The pill gets the two variants `ThresholdNote` renders and no others. If the faculty group needs a third, it adds it then, with a real use behind it.
- **No `.table`, `.modal`, `.dialog`, `.empty-state`, `.badge`, `.tabs`, `.tooltip`, or `.avatar`.** Each has at most one implementation, and every one of those lives in `scaffolding.css` on a page nobody has designed yet.
- **No spacing scale tokens.** The 2/4/8/12/16/20/32 rhythm is real and should be documented in the `components.css` header, but `--space-3` in place of `12px` costs readability in a codebase this size, cannot be theme-switched, and would invite a rewrite of every margin in every file. Radii are tokenised and spacing is not, because a radius is a component-chrome constant reused across primitives and a margin is a per-layout decision.
- **Radius tokens are for component chrome only** — cards, buttons, inputs, callouts, pills. The decorative radii on the data visuals (`.attendance-bar-track` 5px, `.trend-track` and `.lecture-mark` 4px, `.lecture-status::before` 3px, `.callout-mark`'s `50%`) are tuned to their own geometry, are not on the scale, and stay as literal values.
- **`scaffolding.css` is not touched and does not shrink here.** This spec removes duplication between *designed* files; the eleven undesigned pages keep borrowing exactly what they borrow today. Migrating them onto the vocabulary is what groups 3 and 4 do, page by page, with the approval gate `/frontend-maker` has and this spec does not.

**How a page uses a primitive**

- **A page composes primitives and adds its own hook for what is page-specific**: `className="btn btn--secondary sa-toggle"`, where `.sa-toggle` keeps `white-space: nowrap` and the open-class tint, and `.btn--secondary` keeps the fill and hover. Layout, positioning, page-only states, and data-driven styling stay on the page hook.
- **A page never restyles a primitive to suit itself.** `.card { padding: 16px }` in a page file is the failure this spec exists to prevent — it silently redesigns login and both student pages. Override on the page's own selector (`.sa-class { padding: 16px }`) instead, which the manifest order already makes work.
- **Changing a primitive is a deliberate, announced change to every page using it**, and the pages must be named. This is the opposite of the `scaffolding.css` rule and the difference is intent: those classes were never shared on purpose, these are.

**This is a refactor — three named exceptions aside**

- **The finished pages must look the same.** Verify `/login`, `/student`, and `/student/attendance` against `main` at 360px, 768px and 1440px, in both light and dark, before calling this done.
- The three accepted, deliberate changes, all on the temporary shell button, all of them convergences rather than redesigns: `.app-logout` gains the `8px` radius in place of `6px`, gains `8px 14px` padding in place of `6px 12px`, and gains the hover transition, tap-highlight suppression, and `touch-action` it never had. It appears on all nine signed-in pages; that is expected and is the point.
- **Everything load-bearing survives the move.** In particular: `overflow-wrap: anywhere` on the callout; `font: inherit` and the `1rem`/`0.9rem` control sizes that keep iOS from zooming; `min-width: 0` on fields and controls; `touch-action: manipulation`; `.student-home-link:focus-visible { outline: none }` and its stretched `::after` ring, which must still win over the new global ring; and every `prefers-reduced-motion` block.
- **No behaviour changes.** No component gains or loses state, props, a handler, an effect, a request, or a conditional branch. The five JSX diffs are `className` strings and nothing else.
- **Accessibility does not regress.** One `<h1>` per page, landmarks unchanged, the skip link still first and still focusable, `aria-current="page"` still on the current nav item, focus visible on every interactive element, and nothing conveyed by colour alone — the pill still says "met"/"below" in words, the lecture marks are still solid/hollow.

**The project-wide rules, which this spec is unusually able to break**

- **Never write a raw colour outside `tokens.css`.** A new shared file is the likeliest place for a stray hex to enter and be inherited by every page at once.
- **Both palettes.** Every primitive is checked in light and dark; every foreground/background pair in use clears 4.5:1.
- Frontend-only: authorization, role guards, academic data, and biometric handling are untouched. No `ProtectedRoute`, no `@role_required`, no endpoint, no secret.

## Definition of done

**The vocabulary exists**

- [ ] `frontend/src/styles/components.css` exists, is imported between `base.css` and `login.css` in the manifest, and defines exactly the five families above and nothing else.
- [ ] Every primitive in the file has at least two pre-existing implementations behind it (the pill excepted and noted as such in the file), and no variant exists without a caller in the migrated markup.
- [ ] `--radius-sm`, `--radius-md` and `--radius-pill` are declared once in `tokens.css`, outside the theme blocks, and are used by the primitives.
- [ ] No spacing token was added, and no colour token was added.
- [ ] `components.css` contains no raw colour value, and neither does any other file outside `tokens.css`.

**The duplication is gone**

- [ ] The card's `--surface` + `1px --border` + `12px` radius triple appears once in the project, not four times.
- [ ] The secondary button's accent-border hover appears once, not twice; `.app-logout`'s rule is gone from `shell.css`.
- [ ] The error callout's box appears once; `.auth-error` and `.sa-error` no longer each declare it.
- [ ] The 2px/2px focus ring is declared once, in `base.css`; the three page-level copies are gone.
- [ ] `login.css`, `shell.css`, `student-dashboard.css` and `student-attendance.css` are collectively smaller than before, and the four files plus `components.css` are collectively smaller than the four files were.
- [ ] `scaffolding.css` is byte-identical.

**Nothing looks different**

- [ ] `/login` renders identically to `main` at 360px, 768px and 1440px, in light and dark: the centred 400px card, the type hierarchy, the bordered error callout with its glyph, the disabled in-flight button and its `progress` cursor.
- [ ] `/student` renders identically: the card, its chevron, the hover lift, the pressed tint, the stretched focus ring, and the 480px padding reflow.
- [ ] `/student/attendance` renders identically: the overall gauge, weakest-first class list, threshold markers, `ThresholdNote` pills, the trend, the lecture strip, the date filter, the "not taken yet" state, and the 693px and 1160px reflows.
- [ ] The nine signed-in pages show the logout button in its new converged size, and no other visual change.
- [ ] An unbroken 90-character error message at 360px still puts no horizontal scrollbar on the document, on `/login` and on `/student/attendance`.
- [ ] Focusing any input on iOS-width viewports does not zoom the page.

**Nothing behaves differently**

- [ ] The five modified JSX files differ from `main` only in `className` strings.
- [ ] Login, logout, the role redirects, the student attendance requests, class open/close, and the date filter all behave as before.
- [ ] `npm run lint` reports no warning other than the pre-existing `AuthContext.jsx:51` one.
- [ ] `npm run build` passes.
- [ ] Nothing under `backend/` changed, and `pytest` still passes.

**The next group can use it**

- [ ] `autoattend-css-designer.md` documents the five families, how a page composes them, that a primitive is never restyled for one page, and that a sixth primitive is a report note rather than a licence.
- [ ] `CLAUDE.md` lists `components.css` in the stylesheet manifest with its governing rule, and records group 3 (faculty) as next.
