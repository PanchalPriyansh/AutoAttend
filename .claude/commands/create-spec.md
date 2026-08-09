---
description: Create a feature spec and feature branch for the next AutoAttend step
argument-hint: "Step number and feature name e.g. 02 authentication"
allowed-tools: Read, Write, Glob, Bash(git:*)
---

Create a spec file and feature branch for the next AutoAttend development step.

Always follow the project's `specifications.md` and existing project conventions.

User input: $ARGUMENTS

## Step 1 — Check working directory is clean

Run `git status` and check for uncommitted, unstaged, or untracked files.

If any exist, stop immediately and tell the user to commit or stash changes before proceeding.

DO NOT CONTINUE until the working directory is clean.

## Step 2 — Parse the arguments

From `$ARGUMENTS` extract:

1. `step_number` — zero-padded to 2 digits:
   `2 → 02`, `11 → 11`

2. `feature_title` — human-readable title in Title Case.
   Example: `Authentication` or `Faculty Attendance Workflow`

3. `feature_slug` — git and file-safe slug:
   - Lowercase
   - Kebab-case
   - Only a-z, 0-9 and -
   - Maximum 40 characters

4. `branch_name` — format:
   `feature/<feature_slug>`

If these cannot be inferred, ask the user to clarify before proceeding.

## Step 3 — Check branch name is not taken

Run `git branch` to list existing branches.

If `branch_name` is already taken, append a number:

`feature/attendance-01`, `feature/attendance-02`, etc.

## Step 4 — Switch to main and pull latest

Run:

```bash
git checkout main
git pull origin main
```

## Step 5 — Create and switch to the feature branch

Run:

```bash
git checkout -b <branch_name>
```

## Step 6 — Research the codebase

Read these before writing the spec:

- `specifications.md` — master AutoAttend requirements
- `CLAUDE.md` — if present, for project conventions and roadmap
- Existing frontend/backend structure
- Existing relevant API, model, component, and service files
- All existing feature specs to avoid duplication

Check whether the requested feature is already implemented or already covered by an existing spec.

If it is already complete, warn the user and stop.

## Step 7 — Write the spec

Generate a focused feature specification with this structure:

---

# Spec: <feature_title>

## Overview

One paragraph describing what this feature does, why it is needed, and where it fits in the AutoAttend roadmap.

## Depends on

Previous features or project components required before implementation.

## APIs

Every new or modified API needed:

- `METHOD /api/...` — description — required role

If no API changes are needed: state "No API changes".

## Database changes

Any new or modified MongoDB collections, documents, fields, or relationships.

If none: state "No database changes".

## Frontend

- **Create:** new React components/pages
- **Modify:** existing components/pages and required changes

## Backend

- **Create:** new backend files/modules
- **Modify:** existing backend files/modules and required changes

## Files to change

Every existing file that will be modified.

## Files to create

Every new file that will be created.

## New dependencies

Any new packages required.

If none: state "No new dependencies".

## Rules for implementation

Specific constraints that must be followed.

Always consider:

- Follow the existing React + Flask + MongoDB architecture.
- Keep frontend and backend responsibilities separate.
- Enforce authentication and role-based authorization on the backend.
- Keep academic data database-driven where required.
- Do not expose secrets or sensitive biometric data.
- Do not introduce unnecessary dependencies.
- Preserve existing functionality.

Add feature-specific constraints where necessary.

## Definition of done

A specific, testable checklist. Each item must be verifiable through the application, API, database, or appropriate automated tests.

---

## Step 8 — Save the spec

Save to:

`.claude/specs/<step_number>-<feature_slug>.md`

## Step 9 — Report to the user

Print a short summary in this exact format:

```text
Branch:    <branch_name>
Spec file: .claude/specs/<step_number>-<feature_slug>.md
Title:     <feature_title>
```

Then tell the user:

"Review the spec at `.claude/specs/<step_number>-<feature_slug>.md` then begin implementation."

Do not print the full spec in chat unless explicitly asked.