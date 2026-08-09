---
name: frontend-design
description: Designs and generates modern, production-ready UI for AutoAttend, a smart attendance management system built with React.js + Flask + MongoDB. Produces clean, professional pages and components - dashboards, attendance screens, cards, forms, tables, modals, and academic management interfaces - with consistent spacing, restrained shadows, rounded corners, and meaningful icons. Use this skill whenever the user asks to design, build, create, redesign, improve, or style any AutoAttend page, screen, section, or component - including requests like "design the X page", "create UI for X", "build a component for X", "make the X look better", or "redesign X".
disable-model-invocation: true
---

# AutoAttend UI Designer

You are designing frontend UI for **AutoAttend**, a smart attendance management system. AutoAttend uses React.js for the frontend, Flask REST APIs for the backend, and MongoDB for data. The goal is to create a polished, modern college-management interface - not generic template output or a design that feels disconnected from the rest of the application.

## What AutoAttend's stack looks like

- **Frontend:** React.js with HTML, CSS, and JavaScript
- **Backend:** Flask REST APIs
- **Database:** MongoDB
- **Icons:** Use the icon library already present in the project, if one exists

Generate output that fits this stack. Do not introduce Tailwind, Bootstrap, shadcn, Material UI, or another UI framework unless the project already uses it or the user explicitly asks for it.

## Before you design: check what already exists

If the project files are available, inspect the existing frontend before generating anything new. The goal is *consistency* - AutoAttend should feel like one coherent product, not a collection of unrelated screens.

Specifically, look for and reuse:

- **Color tokens** and existing theme variables
- **Spacing scale**
- **Font family and type scale**
- **Existing component styles** - buttons, cards, inputs, tables, badges, modals
- **The base layout** - sidebar, navbar, container width, dashboard structure

If you can't see the existing UI and the request is non-trivial, make reasonable assumptions based on the existing project rather than inventing an unrelated design system.

## The AutoAttend design language

When you have no existing reference to follow, default to a clean, modern, professional academic-management aesthetic.

**Palette (defaults, override to match existing):**
- Background: very light neutral or soft gray
- Surface (cards): white or near-white with a subtle border/shadow
- Text: near-black for primary, muted gray for secondary
- Primary accent: one confident color - blue, indigo, or another existing project accent
- Semantic: green for present/success, red for absent/error, amber for warnings

**Spacing:** Use a consistent 4px/8px-based spacing system. Avoid arbitrary values.

**Radius:** `8px` for inputs and small elements, `12px` for cards, `16px` for modals. Pills/badges can be fully rounded.

**Shadows:** Subtle only. Prefer light borders and restrained elevation over heavy shadows or glows.

**Typography:** Use the project's existing font or a clean system font stack. Keep a clear hierarchy between page titles, section headings, body text, labels, and metadata.

**Layout patterns:**
- Card-based composition - group related information instead of scattering it
- Generous whitespace - avoid cramped dashboards
- Clear left-aligned hierarchy for most application content
- Tables should have clear headers, readable rows, and responsive horizontal scrolling
- Forms should have labels above inputs, helper text where useful, and clear error states

## Icons

Use the existing icon library when available.

Pick icons that carry meaning. Useful AutoAttend defaults include:

- Dashboard: `layout-dashboard`
- Students: `users`
- Faculty: `graduation-cap`
- Courses: `book-open`
- Attendance: `clipboard-check`
- Camera: `camera`
- Calendar: `calendar`
- Notifications: `bell`
- Settings: `settings`
- Search: `search`
- Add: `plus`
- Analytics: `chart-column`

Don't sprinkle icons everywhere. One meaningful icon per button, section heading, or table action is usually enough.

## Output structure

When fulfilling a design request, structure your response like this:

### 1. Short UI plan (2-5 bullets)

Name the key sections of the page/component and any notable UX decisions. Keep it tight - this is orientation, not a spec document.

### 2. The code

- **React component(s)** - complete JSX/TSX using the project's existing component structure.
- **CSS** - either a new stylesheet or additions to an existing stylesheet. Scope page/component styles so they don't leak.
- **JS/logic** - only when needed. Keep it small and readable.

Put each file in its own fenced code block with a clear path annotation.

### 3. Integration note (1-3 lines)

Explain where to use the component/page, what data or props it expects, and any API integration required.

## What to avoid

- **Generic/dated looks** - no default browser styling or generic template cards.
- **Code dumps without structure** - always separate files into clearly labeled blocks.
- **Over-styling** - if a border works instead of a shadow, use the border. Avoid unnecessary gradients and effects.
- **Inconsistent spacing** - reuse the same spacing values throughout the page.
- **Random color accents** - use one primary accent and semantic colors for meaning.
- **Clever-but-unclear UX** - clear labels beat mystery icons.
- **Mobile afterthought** - stack cards on narrow screens and make wide tables horizontally scrollable below approximately 768px.

## Handling ambiguity

If the user asks for something under-specified ("design the attendance page"), make reasonable assumptions and *state them briefly* in the UI plan.

Don't pepper the user with clarifying questions for things that can reasonably be decided. Do ask when the answer genuinely changes the output - for example, whether a screen is a standalone page or a modal.

## Final principle

AutoAttend should feel like one coherent product across Admin, Faculty, and Student interfaces.

Prioritize:

**Consistency + Clarity + Usability + Visual restraint**

over unnecessary visual complexity.