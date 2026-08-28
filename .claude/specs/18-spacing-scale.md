# Spec: Spacing Scale

## Overview

`tokens.css` says outright that "There is deliberately no spacing scale -- see
components.css", and `components.css:33` gives the reason: "`--space-3` in place of
`12px` costs more in readability than it returns in a project this size, and a margin is
a per-layout decision in a way a corner is not." That was a decision, not an oversight,
and this spec overturns only half of it. It introduces a **value-named** scale
(`--space-12` *is* 12px), applies it to the 170 spacing declarations already sitting on
the rhythm, and **changes no pixel anywhere**. The 37 declarations that are off the
rhythm keep their literals — which is the point of the exercise as much as the tokens
are: after this pass a bare `10px` in a spacing declaration means "deliberately off the
scale", and the next page that reaches for `13px` is visible in a diff instead of
invisible in it.

It is the fifth of the six specs in the polish pass scoped 2026-08-28, after
`14-confirm-dialog-focus-trap`, `15-muted-on-accent-contrast`, `16-portal-card-link` and
`17-collapsible-nav`. It was ordered last-but-one on purpose, because it is the only one
that touches every page file at once. **`19`, the manual theme toggle, is not part of
this spec.**

**This spec takes `/code-review-feature`, unlike `12`-`17`.** CLAUDE.md's workflow
section says frontend-only work skips the review pipeline because there is no frontend
test runner and a diff read cannot catch a header height or a contrast ratio — and then
names the exception: "Go back to `/code-review-feature` when the blast radius exceeds
what you can directly verify — a change touching many page files at once... A spacing
scale is exactly that case and should say so in its own spec." This is that sentence
being honoured. Sixteen stylesheets change; no browser pass samples all of them.

## Depends on

- `13-component-vocabulary` — `styles/components.css` and the `--radius-*` tokens, which
  are the precedent this scale follows: a constant shared across files, declared once in
  `tokens.css`, outside both theme blocks because it does not change with the palette.
  The paragraph in `components.css` arguing *against* a spacing scale is this spec's
  starting evidence and is rewritten by it.
- The finished redesign — every page is styled by a file named after what it styles, and
  `scaffolding.css` is deleted. The scale is extracted from what those pages converged
  on; it could not have been written before them, for the same reason `12` refused to
  write `components.css` up front.
- `17-collapsible-nav` — the most recent spacing written into `shell.css`, and the
  source of the harness discipline this spec reuses (build the check in the scratchpad,
  and check `git status` for stray harness files before committing).

## APIs

**No API changes.** Nothing in this spec reaches the network.

## Database changes

**No database changes.**

## The scale

Eight values, in `tokens.css`, beside the radii:

```css
--space-2:   2px;
--space-4:   4px;
--space-8:   8px;
--space-12: 12px;
--space-16: 16px;
--space-20: 20px;
--space-24: 24px;
--space-32: 32px;
```

Four decisions are load-bearing.

**The names carry the values, and that is the answer to the objection in
`components.css`.** That paragraph objects to `--space-3` — an *index* name, which hides
the value and forces a lookup at every read. `--space-12` hides nothing: `gap:
var(--space-12)` says twelve pixels as plainly as `gap: 12px` does. What it adds is that
the value is now a member of a named set, so the set can be enumerated, grepped, and
departed from visibly. The objection is answered rather than ignored, and the file's
paragraph is rewritten to say which half of it survived.

**The set is what the pages already use, not what a scale ought to contain.** These are
the eight values the redesign converged on, each appearing in three or more stylesheets:

| token | value | uses | files |
|---|---|---|---|
| `--space-2` | 2px | 8 | 7 |
| `--space-4` | 4px | 21 | 8 |
| `--space-8` | 8px | 39 | 13 |
| `--space-12` | 12px | 84 | 9 |
| `--space-16` | 16px | 35 | 13 |
| `--space-20` | 20px | 11 | 8 |
| `--space-24` | 24px | 8 | 4 |
| `--space-32` | 32px | 5 | 3 |

Counted after substitution, comments excluded: 211 occurrences over the 170
declarations. Three rows were originally written from the single-line census and
undercounted, because the two multi-line safe-area paddings in `base.css` and
`login.css` had not been added yet — they contribute four more `--space-16`, two
more `--space-24` (bringing `base.css` in as its fourth file) and two more
`--space-32`. Corrected here after the code review caught the discrepancy.

No value is added for symmetry or for future use. There is no `--space-40`, no
`--space-48` and no `--space-6`, because nothing in the project would reference them —
the same rule that governs `components.css` ("a primitive is added only when two
designed pages already implement it") applied to constants. Note that `components.css`
currently describes the rhythm as "2/4/8/12/16/20/32": **24px is missing from that list
and is real**, used by `confirm-dialog.css`, `login.css`, `shell.css` and `base.css`'s
safe-area padding. Correcting that sentence is part of the work.

**No token is themed.** The `--space-*` block sits in `:root` outside both palette
blocks and is *not* redeclared under `prefers-color-scheme: dark`. A gap does not change
with the palette any more than a corner does.

**No token is responsive.** No `--space-*` is redefined inside a media query. A page
that tightens at a breakpoint keeps doing it the way it does today — by declaring a
different value on its own selector, measured for that page — and every such
declaration is tokenised in place if its value is on the scale. Redefining the scale
per-breakpoint would make one `var(--space-24)` mean two things and would silently move
pixels on every page that inherited the change, which is the exact failure mode
`components.css`'s "a page never restyles a primitive" rule exists to prevent.

## What gets tokenised, and what does not

The rule is mechanical, so that it can be checked rather than argued:

> A spacing declaration is tokenised **only if every length in it is on the scale**. A
> declaration containing any off-scale length stays wholly literal.

"Spacing declaration" means `gap`, `row-gap`, `column-gap`, `padding`, `margin` and
their longhands. That is 207 declarations across sixteen files. 170 are tokenised; 37
are not.

**A shorthand is never split.** `padding: 10px 12px` does not become `padding: 10px
var(--space-12)`. Half-tokenised shorthands read worse than either alternative and would
imply the 10 was an accident, when the eight declarations carrying it are the project's
input and select padding, tuned to the text inside them. The declaration stays as it is,
and the rule around it is where that tuning belongs.

The 37 that stay literal, with the value that blocks each:

| file:line | declaration | off-scale |
|---|---|---|
| `admin-academics.css:158`, `:227` | `padding: 10px 12px` | 10 |
| `admin-academics.css:282` | `padding: 6px 8px 6px 5px` | 5, 6 |
| `admin-academics.css:339` | `padding: 4px 10px` | 10 |
| `admin-face-enrollment.css:264`, `:298` | `padding: 10px 12px` | 10 |
| `admin-face-enrollment.css:338` | `padding: 8px 8px 8px 5px` | 5 |
| `admin-face-enrollment.css:470` | `padding: 4px 10px` | 10 |
| `admin-face-enrollment.css:595` | `padding: 6px 8px` | 6 |
| `admin-face-enrollment.css:607` | `margin-right: 10px` | 10 |
| `admin-face-enrollment.css:608` | `padding: 5px 10px` | 5, 10 |
| `admin-users.css:294` | `padding: 10px 12px` | 10 |
| `admin-users.css:349`, `:387` | `padding: 8px 8px 8px 5px` | 5 |
| `admin-users.css:486` | `padding: 4px 10px` | 10 |
| `components.css:70` | `padding: 8px 14px` (`.btn`) | 14 |
| `components.css:185` | `padding: 10px 12px` (`.form-field`) | 10 |
| `components.css:322` | `gap: 6px` | 6 |
| `components.css:340` | `padding: 8px 10px` | 10 |
| `components.css:357` | `padding: 10px 12px` | 10 |
| `faculty-attendance.css:241` | `padding: 6px 8px` | 6 |
| `faculty-attendance.css:253` | `margin-right: 10px` | 10 |
| `faculty-attendance.css:254` | `padding: 5px 10px` | 5, 10 |
| `faculty-attendance.css:322` | `padding: 10px 12px` | 10 |
| `faculty-attendance.css:372` | `margin: 0 0 10px` | 10 |
| `faculty-attendance.css:452` | `padding: 10px 18px` | 10, 18 |
| `faculty-history.css:218` | `padding: 10px 12px` | 10 |
| `faculty-history.css:279` | `padding: 8px 8px 8px 5px` | 5 |
| `faculty-history.css:525` | `padding: 10px 18px` | 10, 18 |
| `portal-card.css:42` | `padding: 20px 48px 20px 20px` | 48 |
| `portal-card.css:144` | `margin: 6px 0 0` | 6 |
| `portal-card.css:231` | `padding-right: 36px` | 36 |
| `student-attendance.css:210` | `margin-top: 10px` | 10 |
| `student-attendance.css:288` | `padding: 7px 0` | 7 |
| `student-attendance.css:374` | `margin-top: 14px` | 14 |
| `student-attendance.css:401` | `gap: 6px 10px` | 6, 10 |
| `student-attendance.css:492` | `gap: 5px` | 5 |

These are overwhelmingly **control internals rather than layout**: `10px 12px` is the
input and select padding written eight times, `4px 10px` and `5px 10px` are pill
paddings, `8px 14px` is `.btn`, `8px 8px 8px 5px` is the five accent-bar rows whose left
padding is short by the 3px the bar occupies, and `portal-card.css`'s 48 and 36 are the
chevron gutter measured in `16`. They are tuned to type metrics and to specific
geometry, in the same way `tokens.css` already carves the data-visual radii out of
`--radius-*`: "the small radii on the data visuals... are tuned to their own geometry,
are not on this scale, and stay as literal values where they are used." This spec makes
the identical carve-out for spacing, and says so in the same place.

**Two multi-line declarations are tokenised despite containing a function**, because
every length in them is on the scale — `base.css:43` and `login.css:33`, the safe-area
paddings:

```css
padding: max(var(--space-24), env(safe-area-inset-top)) max(var(--space-16), …) …;
```

**`0` needs no token** and gets none. The 74 declarations that are `0`, `auto`, a
percentage or already a `var()` are untouched.

## Explicitly not spacing

None of the following is tokenised, and no `--space-*` may appear in them:

- **Breakpoints.** `max-width: 480px`, `413px`, `420px` and every other media-query
  width. They are measured per page and the files say so; a breakpoint is a viewport
  measurement, not a gap, and the two happening to share a number would be a coincidence
  the token would make look like a rule.
- **Sizes and positions** — `width`, `height`, `min-*`, `max-*`, `inset`, `top`, `left`,
  `flex-basis`, `translate`. `.skip-link:focus { top: 8px; left: 8px }` stays literal.
- **`border-width`, `border-radius`** (which has its own tokens), `outline-offset`,
  `box-shadow` offsets, `letter-spacing`, `line-height`, `font-size`.
- **Anything inside a data visual** — the gauge track, the lecture strip, the trend
  bars. Their geometry is tuned to itself, exactly as their radii are.

## Frontend

- **Create:** nothing.
- **Modify:** `frontend/src/styles/tokens.css` gains the eight declarations and loses the
  sentence denying they exist; `frontend/src/styles/components.css` has its spacing
  paragraph rewritten and its rhythm list corrected to include 24; the other fourteen
  stylesheets have on-scale spacing literals replaced with `var(--space-*)`. **No `.jsx`
  file changes.** No class name is added, removed or renamed, so no component has any
  reason to.

## Backend

**No backend changes.** Nothing under `backend/` is touched and `pytest` is unaffected.

## Files to change

- `frontend/src/styles/tokens.css` — the scale, and the rewritten header comment
- `frontend/src/styles/components.css` — the rewritten spacing paragraph, plus 4
- `frontend/src/styles/base.css` — 1, plus the safe-area `max()` pair
- `frontend/src/styles/confirm-dialog.css` — 4
- `frontend/src/styles/portal-card.css` — 0 tokenised (all three of its spacing
  declarations are off-scale); touched only if a literal beside a token needs explaining
- `frontend/src/styles/login.css` — 6, plus the safe-area `max()` pair
- `frontend/src/styles/shell.css` — 11, and the deferral list in its header
- `frontend/src/styles/student-dashboard.css` — 2
- `frontend/src/styles/student-attendance.css` — 23
- `frontend/src/styles/faculty-dashboard.css` — 2
- `frontend/src/styles/faculty-roster-row.css` — 2
- `frontend/src/styles/faculty-attendance.css` — 22
- `frontend/src/styles/faculty-history.css` — 24
- `frontend/src/styles/admin-portal.css` — 2
- `frontend/src/styles/admin-academics.css` — 27
- `frontend/src/styles/admin-users.css` — 18
- `frontend/src/styles/admin-face-enrollment.css` — 20
- `CLAUDE.md` — the "Stylesheets" section, the App shell row's deferral list, and "Next
  planned feature"

## Files to create

**No new files.** In particular, no `styles/spacing.css`: the scale is a set of
constants, and `tokens.css` is where this project's constants live. A file of its own
would add a fourth kind of entry to the manifest for eight declarations.

## New dependencies

**No new dependencies.** `frontend/package.json` is byte-identical afterwards. No
PostCSS plugin, no Sass, no design-token tool — CSS custom properties are already what
this project uses for the palette and the radii.

## Rules for implementation

**Scope**

- The spacing scale only. **No theme toggle** — that is `19`.
- **Zero pixel change is the constraint, not an aspiration.** Every rendered surface, in
  both palettes, at every width, is identical to `main`. A declaration whose value would
  have to move to fit the scale is not tokenised; it stays literal. If a value looks
  wrong, that is a finding to report, not to fix here.
- No selector is added, removed or changed. No rule is reordered. No declaration is added
  or deleted. The diff is a value substitution and a set of comment rewrites, and nothing
  else.
- **No primitive is restyled to absorb a page's spacing.** `components.css`'s rule holds:
  a page that overrides `.card`'s padding on its own selector keeps doing exactly that,
  with the value tokenised in place. Consolidating those overrides is a redesign and is
  not in this spec.

**Applying the scale**

- Substitute only where every length in the declaration is on the scale. Never split a
  shorthand.
- `var(--space-N)` where `N` is the pixel count, always. A token whose name does not
  match its value is a bug this spec's verification is built to catch.
- The scale is declared once, in `:root` in `tokens.css`, outside both theme blocks, and
  is never redeclared — not in the dark block, not in a media query, not in a page file.
- Do not tokenise anything in the "Explicitly not spacing" list, and do not tokenise a
  length in a `box-shadow`, a `transform`, or a data-visual dimension merely because its
  number matches.

**Comments**

- `tokens.css`'s "There is deliberately no spacing scale -- see components.css" is
  replaced by what the scale is, what it deliberately excludes, and why the exclusions
  are literal — mirroring the radii paragraph immediately above it, which already makes
  the same carve-out for the data visuals.
- `components.css:30-35` is rewritten rather than deleted. It must record that the
  objection was to *index* naming, that value-naming answers it, and that the rhythm it
  lists was missing 24.
- `shell.css:18`'s "Still deferred on purpose, each its own spec: a spacing scale, and a
  manual theme toggle" drops the spacing scale.

**Project-wide**

- Frontend-only: no route, no endpoint, no academic data, no biometric data, no secret.
- No raw colour is introduced anywhere; no colour declaration is touched at all.
- Preserve existing functionality. The nine signed-in pages plus login and NotFound still
  render, navigate and submit exactly as they do on `main`.

**Deliberately out of scope**

- Normalising the 37 off-scale declarations onto the scale. Considered and rejected when
  this spec was scoped: it would change pixels on nearly every page and redesign controls
  that were individually tuned, which is a design pass and not a tokenisation.
- A typography scale, a size scale, a breakpoint scale, or an elevation scale.
- The `/admin/face-enrollment` picker arithmetic, and the reported-but-declined guideline
  findings (Title Case, URL-reflected filter state, list virtualization).

## Definition of done

**The scale**

- [ ] `tokens.css` declares exactly the eight tokens above, in `:root`, outside both
      theme blocks, and every one satisfies `--space-N: Npx`.
- [ ] No `--space-*` is redeclared anywhere — not under `prefers-color-scheme: dark`, not
      in any media query, not in any page file. Each token has exactly one declaration.
- [ ] No `--space-*` token is declared that nothing references, and no spacing token
      exists outside the eight.

**Zero pixel change — proved at source, not sampled**

- [ ] Reverse-substituting `var(--space-N)` → `Npx` across all seventeen stylesheets
      reproduces `main`'s stylesheets **byte-for-byte once comments are stripped**. This
      is the primary evidence, and it is total rather than sampled: it covers every rule,
      every media query and every state, including ones no browser pass would reach. The
      script that performs it lives in the scratchpad and is **not committed** — check
      `git status` for stray harness files, as `17` had to.
- [ ] The only non-comment difference the check reports is the eight added declarations
      in `tokens.css`.
- [ ] Every one of the 37 off-scale declarations listed above is still present, with its
      literal value unchanged.

**It still parses and still applies**

- [ ] `npm run build` passes, and the built `dist/assets/*.css` contains the eight
      `--space-*` declarations and no unresolved or misspelled `var(--sp…)` reference.
- [ ] A real browser pass over the signed-in pages at 360, 768 and 1440px, in light and
      dark, shows no dropped declaration — nothing collapsed to `0` or to the initial
      value, which is what a `var()` typo produces rather than an error. Sampled on
      purpose: the source diff above is what proves equivalence; this proves the
      stylesheet reaches the page.
- [ ] The safe-area paddings in `base.css` and `login.css` still resolve — at ≤480px with
      zero insets, `#root` still computes 24/16 and the login screen 32/16.

**Gates**

- [ ] `npm run lint` reports no new warning (the pre-existing `AuthContext.jsx:51` one may
      remain).
- [ ] `frontend/package.json` is byte-identical, and no `.jsx` file changed.
- [ ] Nothing under `backend/` changed, and `pytest` still passes.
- [ ] **`/code-review-feature 18-spacing-scale` has been run**, per CLAUDE.md's
      blast-radius rule, and its findings are reported to the user before any of them are
      applied.

**Records updated**

- [ ] `tokens.css` no longer says there is deliberately no spacing scale, and states what
      the scale excludes and why.
- [ ] `components.css`'s spacing paragraph says which half of its objection survived, and
      its rhythm list includes 24.
- [ ] `shell.css` no longer lists a spacing scale among its deferrals.
- [ ] CLAUDE.md's "Stylesheets" section documents the scale and the literal carve-out; the
      App shell row drops the spacing scale from its deferrals; "Next planned feature"
      carries only the theme toggle as `19`.
