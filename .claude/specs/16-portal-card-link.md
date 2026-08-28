# Spec: Portal Card Extraction

## Overview

`/student`, `/faculty` and `/admin` each end at the same control: a card whose whole area is one navigation target — a label, a sentence, a chevron, a stretched hit area, and the hover/press/focus mechanics tuned around them. It was built three times, once per group of the redesign, and each time deliberately: `faculty-dashboard.css:15` and `admin-portal.css:14` both say in as many words that "this goes somewhere" should look identical in all three portals, and `admin-portal.css:18` records that the third implementation is the one that should trigger an extraction. This spec is that extraction.

It is **item 3 of the four-item polish pass** scoped 2026-08-28, after `14-confirm-dialog-focus-trap` and `15-muted-on-accent-contrast`. The set is closed — `navigation.js` has three roles and there is no fourth portal — so nothing is being generalised speculatively; three existing implementations are being replaced by one.

The duplication is not only in CSS. The three route files render the same eight lines of JSX with three different class prefixes, so this extracts **a component**, not a class: `PortalCard.jsx` plus `styles/portal-card.css`, on the `faculty-roster-row.css` / `confirm-dialog.css` precedent. See "The decision" below for why it is not a `components.css` primitive.

Behaviour does not change. `/student` gains four small, deliberate visual changes where it converges onto the other two; `/faculty` and `/admin` are pixel-identical to `main`.

## Depends on

- `12-app-shell` — `navigation.js`, the single source of each role's links, and the three landing pages that read it. `navigation.js` is **not modified**.
- `13-component-vocabulary` — `.card`, which all three cards already compose and which continues to carry the fill, the hairline and the corner.
- The group 2 / 3 / 4a redesigns, which produced the three implementations being collapsed.
- The `faculty-roster-row.css` and `confirm-dialog.css` precedent: a component rendered by more than one page gets its own stylesheet, loaded after the primitives it composes and before every page that renders it.

## The three implementations, and every way they differ

Markup first. `StudentDashboard.jsx`, `FacultyDashboard.jsx` and `AdminPortal.jsx` are identical apart from the role string, the `<ul>` class, and the three class prefixes:

```jsx
<li className="<prefix>-card card" key={item.to}>
  <Link className="<prefix>-link" to={item.to}>{item.label}</Link>
  <p className="<prefix>-desc">{item.description}</p>
</li>
```

CSS second. Of roughly 90 lines each, these are **the only** differences:

| | `/student` | `/faculty` | `/admin` |
|---|---|---|---|
| card `padding-right` | 56px | 48px | 48px |
| chevron `right` | 24px | 20px | 20px |
| narrow breakpoint | `max-width: 480px` | `max-width: 420px` | `max-width: 420px` |
| narrow `padding-right` | 40px | 36px | 36px |
| narrow chevron `right` | 16px | 12px | 12px |
| `-desc` `font-size` | 0.9rem | 0.85rem | 0.85rem |
| `-desc` `line-height` | (inherited) | 1.45 | 1.45 |
| link `::after` radius | `12px` | `var(--radius-md)` | `var(--radius-md)` |

Everything else is byte-identical across the three: the `position: relative` + transition block, the `::before` composited shadow layer (`inset: -1px`, `var(--shadow)`, opacity 0→1), the two-border chevron (`top: 27px`, 8×8, `rotate(45deg)`, `translateX(3px)` on hover), the link typography (`--text-h`, 1.15rem, 500, 1.3, no underline at rest, `overflow-wrap`, `touch-action`, `-webkit-tap-highlight-color: transparent`), the stretched `::after` hit area, all four hover rules, both `:has(.…-link:active)` rules, the two `:focus-visible` rules, and the `prefers-reduced-motion` block.

`border-radius: 12px` in `student-dashboard.css:112` and `var(--radius-md)` are the same value — `tokens.css:56` — so that row is a naming difference, not a visual one.

What is **not** shared, and stays with each page: the `<ul>` and its grid. `.student-home` is a single 600px-capped door; `.faculty-home` is a measured two-up (`minmax(min(320px, 100%), 1fr)`, capped 860px); `.admin-portal` is a board with a `min-width: 1000px` rule that drops the floor to 300px so a 1024px laptop keeps three across. Those are three different layouts with three different arguments behind them, and each keeps its own file and its own comments.

## APIs

**No API changes.** Nothing in this spec reaches the network.

## Database changes

**No database changes.**

## Frontend

- **Create:**

  - `frontend/src/components/layout/PortalCard.jsx` — the `<li>` above, written once. Named props rather than the whole nav item:

    ```jsx
    function PortalCard({ to, label, description }) { … }
    ```

    This is a deliberate one-line deviation from the sketch in the approval, which passed `item={item}`: named props keep the component independent of `navigation.js`'s object shape, and match the destructured-named-props convention every other component in the project already follows (`ConfirmDialog`, `StudentStatusRow`). No `className` escape hatch and no `variant` prop — all three renderers converge completely, so a knob would have no second value to take.

    It renders the `<li>` because the pages render the `<ul>`; the list stays a list. A JSDoc header states what it is, which three pages render it, and that it is styled by its own stylesheet — the shape `ConfirmDialog` and `StudentStatusRow` already use. It lives in `components/layout/` beside `AppShell.jsx` and `NavBar.jsx`, because like them it belongs to the frame rather than to one role's data.

  - `frontend/src/styles/portal-card.css` — the ~90 shared lines, on `.portal-card` / `.portal-card-link` / `.portal-card-desc`, with a header on the model of `faculty-roster-row.css`: one component, three renderers, neither a page nor a primitive, and what the component owns versus what each page owns. The rules move **unchanged in shape**; the per-page comments that explain a mechanism (the composited shadow layer, why the chevron is borders and not text, why the hit area is CSS and not a wrapping `<a>`, why the tap highlight is suppressed) are carried across rather than rewritten, and the ones that only say "all three portals do this identically — see admin-portal.css" are dropped, because after this there is one copy to see.

- **Modify:**

  - `frontend/src/routes/StudentDashboard.jsx`, `frontend/src/routes/FacultyDashboard.jsx`, `frontend/src/routes/AdminPortal.jsx` — each drops the `<li>` body and the now-unused `Link` import, and maps to `<PortalCard key={item.to} to={item.to} label={item.label} description={item.description} />`. The `<ul>` and its class are untouched.

  - `frontend/src/styles/student-dashboard.css` — keeps `.student-home` (the grid, the 600px cap) and its header; loses `.student-home-card`, `-link`, `-desc` and both media blocks.

  - `frontend/src/styles/faculty-dashboard.css` — keeps `.faculty-home` and the measured `minmax`/cap comment; loses `.faculty-home-card`, `-link`, `-desc` and both media blocks.

  - `frontend/src/styles/admin-portal.css` — keeps `.admin-portal`, the laptop-band `min-width: 1000px` rule and their comments; loses `.admin-portal-card`, `-link`, `-desc` and both media blocks. Its header note that "this is now the third implementation … the report on this pass recommends extracting it" is replaced by a pointer to `portal-card.css`.

  - `frontend/src/index.css` — one `@import './styles/portal-card.css'` and a line in the manifest comment. Position: **after `confirm-dialog.css`, before `login.css`** — i.e. with the other shared components, above every page that renders it. `confirm-dialog.css`'s placement is the right precedent rather than `faculty-roster-row.css`'s: the roster row sits low because its two pages override it with plain later selectors, and nothing here overrides anything. The manifest comment's "two shared-component files" paragraph becomes three.

  - `CLAUDE.md` — remove the `.card--link` bullet from the polish-pass list in "Next planned feature", leaving only the shell items; add `portal-card.css` to the stylesheet map; and update the shared-component rule, which currently says "There are two", to name the third.

- **Not modified:** `navigation.js`, `AppShell.jsx`, `NavBar.jsx`, `components.css`, `tokens.css`, `base.css`, `shell.css`, and every non-portal page.

## Backend

**No backend changes.** Nothing under `backend/` is created, modified, or deleted; `pytest` is unaffected and must still pass.

## Files to change

- `frontend/src/routes/StudentDashboard.jsx`
- `frontend/src/routes/FacultyDashboard.jsx`
- `frontend/src/routes/AdminPortal.jsx`
- `frontend/src/styles/student-dashboard.css`
- `frontend/src/styles/faculty-dashboard.css`
- `frontend/src/styles/admin-portal.css`
- `frontend/src/index.css`
- `CLAUDE.md`

## Files to create

- `frontend/src/components/layout/PortalCard.jsx`
- `frontend/src/styles/portal-card.css`

## New dependencies

**No new dependencies.** `frontend/package.json` is byte-identical afterwards.

## The convergence, and what /student gives up

The extraction is not mechanical: `/student` is the odd one out on four values, and one of them is a real regression in one band. All four resolve **towards `/faculty` and `/admin`**, on the principle those two files already state — the same act should look the same in all three portals — and because two-against-one is the only tiebreak available where neither value is wrong.

1. **Gutter 56 → 48, chevron 24 → 20.** Strictly more measure at every width above the breakpoint. No downside.
2. **Description 0.9rem → 0.85rem, plus `line-height: 1.45`.** A smaller face cannot add lines; the explicit line-height is what the other two set and it slightly opens the sentence. `/student`'s description is the longest of the five in `navigation.js` (31 words), on the narrowest card, so this is the page that benefits most.
3. **Link `::after` radius `12px` → `var(--radius-md)`.** Identical computed value. Pure hygiene: a raw length where the project has a token.
4. **Breakpoint 480 → 420 — the one real trade.** Between 421 and 480 the student card today drops to a 40px gutter; afterwards it holds 48. That band loses 8px of measure. Below 421 it drops to 36 instead of 40 and gains 4px. `student-dashboard.css:163` argues 480 from the 56px gutter — "that number is the 56px right gutter" — and the gutter is now 48, which is the exact premise `faculty-dashboard.css:222` uses to choose 420 instead ("this card's gutter starts at 48 … it is already the tighter of the two, so it stays as designed for longer"). So the student file's own reasoning, applied to the new gutter, produces 420. The 421–480 band must still be checked rather than assumed: the Definition of done requires the description not to gain a line at 480, 440 or 421.

## The decision, and what it rules out

The polish-pass entry left this open between `components.css` and a `styles/portal-card.css`. It is a component file — and the extraction takes the JSX with it, which the entry did not consider.

- **A `components.css` primitive (rejected).** It satisfies the letter of the rule — three independent implementations, more than the two required — but not its shape. Every primitive in that file is one class a page composes onto its own element (`.card`, `.btn` + variants, `.callout`, `.pill`, `.form-field`); this is four coupled classes, two pseudo-elements, and a `:has()` relationship *between* them, and a page cannot compose half of it. It would also put ~90 lines of one control into the file every page loads, and set a precedent for coordinated-set primitives that the next contributor would reasonably follow.
- **CSS-only, in `styles/portal-card.css` (rejected).** Better, and it was the entry's own second option, but it fixes half the duplication: the three route files would still each write the same `<li>`, `<Link>` and `<p>`, and a file named after a component that is not a component is the same "dependency nothing declares" that moved the roster row out of `faculty-attendance.css`.
- **Doing nothing (rejected).** Three copies is where a fourth comes from, and the three have already drifted on four values — including a raw `12px` that no longer reads as `var(--radius-md)`. The drift is the evidence.

**What is chosen** makes this exactly the case the project already has a rule for: one component, several renderers, its own stylesheet, loaded before every page that renders it. No rule bends, and `components.css` still holds only single composable classes.

Deliberately **not** done here: no `.modal`, no `.table`, no spacing scale, and no move of `ConfirmDialog` or `StudentStatusRow`. This spec extracts one control.

## Rules for implementation

**Fidelity**

- **`/faculty` and `/admin` must be pixel-identical to `main`** at every width, in both themes, at rest and in hover, press and focus. Every value they contribute is carried across exactly: `padding: 20px 48px 20px 20px`, chevron `top: 27px` / `right: 20px`, `0.85rem` / `1.45`, and the 420px block at `36px` / `12px`.
- **The mechanisms move unchanged.** The shadow stays on the composited `::before` layer at `inset: -1px`; the chevron stays two borders with `pointer-events: none`; the hit area stays the link's `::after` rather than a `<p>` moved inside the `<a>`; the focus ring stays on that overlay with the `<a>`'s own outline suppressed; `-webkit-tap-highlight-color: transparent` stays. If any of these looks removable during the move, it is not — each has a comment saying why it exists.
- **The `prefers-reduced-motion` block comes too**, including the `:hover::after` rule that pins the chevron's rotation.

**Ownership**

- **The component owns the card; each page owns the grid.** Nothing about `.student-home`, `.faculty-home` or `.admin-portal` moves into `portal-card.css`, and no per-page override of `.portal-card` is added — there is nothing left to differ about. If a page appears to need one, that is a signal the convergence above was done wrong.
- **No page hook alongside the shared class.** The markup is `className="portal-card card"`, not `"portal-card card student-home-card"`. A hook with no rules behind it is dead weight.
- **No new token, no new primitive, no raw colour.** Every colour in the moved rules is already a `var(--…)` and stays one.

**Component**

- Named props (`to`, `label`, `description`), destructured in the signature, no `PropTypes` — the project uses none. A JSDoc header on the model of `ConfirmDialog`'s.
- It renders the `<li>`; the pages keep their `<ul>`. `key` stays on the `<PortalCard>` in each page's `.map()`.
- Presentational only: no state, no effect, no fetch, no role logic. It never reads `navigation.js` itself — role filtering stays where it is, and this component is only shown what its caller passes.
- The three pages keep using `navigationFor(role)`. Nothing here changes what any role is *shown*, and nothing here is an authorization boundary: `ProtectedRoute` and `@role_required` are untouched.

**Project-wide**

- Frontend-only: no route, no endpoint, no academic data, no biometric data, no secret.
- Preserve existing functionality: all five destinations still navigate, from a click anywhere on the card and from the keyboard.

**Deliberately out of scope**

- **The fourth polish item** (the shell's collapsible nav, spacing scale, theme toggle) — its own spec, its own session.
- **The `/admin/face-enrollment` picker arithmetic** and the reported-but-declined guideline findings.
- **Any change to the three grids.** `.admin-portal`'s laptop-band rule and `.faculty-home`'s measured floor are correct and stay exactly as they are.

## Definition of done

**The component**

- [ ] `frontend/src/components/layout/PortalCard.jsx` exists, takes `to` / `label` / `description`, renders `<li className="portal-card card">` containing a `<Link>` and a `<p>`, and holds no state, effect, or role logic.
- [ ] All three route files render it; none of them still contains a `<li>`, a `<Link>`, or a `<p>` of its own, and the unused `Link` import is gone from each.
- [ ] `grep -r "student-home-card\|student-home-link\|student-home-desc\|faculty-home-card\|faculty-home-link\|faculty-home-desc\|admin-portal-card\|admin-portal-link\|admin-portal-desc" frontend/src` returns nothing.

**The stylesheet**

- [ ] `frontend/src/styles/portal-card.css` holds the whole control — base, `::before`, chevron, link, hit area, description, four hover rules, both `:has(:active)` rules, both focus rules, the 420px block and the reduced-motion block — and nothing about any page's grid.
- [ ] `student-dashboard.css`, `faculty-dashboard.css` and `admin-portal.css` each retain their `<ul>` rule and their layout comments, and contain no `-card`, `-link` or `-desc` rule.
- [ ] `index.css` imports it between `confirm-dialog.css` and `login.css`, and the manifest comment describes three shared-component files rather than two.
- [ ] No raw colour and no raw radius anywhere in the new file; `border-radius: 12px` is now `var(--radius-md)`.

**/faculty and /admin unchanged**

- [ ] `/faculty` and `/admin` are pixel-identical to `main` at 360, 420, 480, 768, 1024 and 1440px, in light and dark.
- [ ] On both, hover raises the shadow and slides the chevron 3px, press tints `--accent-weak` and drops the shadow, and `Tab` rings the whole card — exactly as on `main`.
- [ ] `/admin` is still three across at 1024 and at 1440, and one across below 720.
- [ ] `/faculty` is still two across at 768 and one across at 719.

**/student converged**

- [ ] The card's gutter is 48px and its chevron sits 20px in, above 420px wide.
- [ ] The description renders 0.85rem / 1.45.
- [ ] At 480, 440 and 421px the description sets **no more lines** than it does on `main` at the same width — measured, not assumed. If any of the three gains a line, the breakpoint decision is reopened in this spec rather than worked around in a page file.
- [ ] At 420 and below the gutter is 36px and the chevron 12px.
- [ ] The 600px cap, the 16px gap and the page's single-door layout are unchanged.

**Behaviour**

- [ ] On all three pages a click anywhere on the card — on the padding, on the description, on the chevron — navigates.
- [ ] The link's accessible name is still the label alone, not the label plus the description (verified in the accessibility tree).
- [ ] Keyboard: `Tab` reaches one stop per card, the ring outlines the whole card, and `Enter` navigates.
- [ ] With `prefers-reduced-motion: reduce` no transition runs and the chevron does not slide on hover.

**Gates**

- [ ] `npm run lint` reports no new warning (the pre-existing `AuthContext.jsx:51` one may remain).
- [ ] `npm run build` passes.
- [ ] The built stylesheet contains one copy of the card's rules where it previously contained three.
- [ ] `frontend/package.json` is byte-identical; nothing under `backend/` changed, and `pytest` still passes.

**Records updated**

- [ ] `CLAUDE.md` no longer lists `.card--link` as an outstanding polish-pass item; the stylesheet map lists `portal-card.css`; and the shared-component rule names three files, not two.
- [ ] `portal-card.css`'s header states which three pages render it and where the boundary with each page's grid falls.
- [ ] `admin-portal.css` no longer claims to be the third implementation awaiting extraction.
