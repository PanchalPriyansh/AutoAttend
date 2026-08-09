---
description: Runs parallel security and quality code review for a specific AutoAttend feature. Pass the spec name as argument e.g. /code-review-feature 03-authentication
allowed-tools: Bash(git diff), Bash(git diff --staged)
---

Run the full code review pipeline for the feature specified in `$ARGUMENTS`.

If no argument is provided, stop immediately and say:

"Please provide a spec name. Usage: /code-review-feature <spec-name> e.g. /code-review-feature 03-authentication"

If `.claude/specs/$ARGUMENTS.md` does not exist, stop immediately and report:

"Spec file not found at .claude/specs/$ARGUMENTS.md. Please check the spec name and try again."

## Pre-flight Check

Before invoking any subagents, collect the diff:

- Run `git diff` for unstaged changes.
- Run `git diff --staged` for staged changes.
- Combine both into a single diff.

If both are empty, stop immediately and say:

"No changes detected. Implement the feature before running code review."

---

## Step 1: Parallel Review

Invoke both subagents simultaneously with the same context.

**autoattend-security-reviewer** receives:

- The combined diff from the pre-flight check.
- Spec file for context: `.claude/specs/$ARGUMENTS.md`
- Relevant changed source files.
- Instruction: Review only the changed code for security vulnerabilities. Do not comment on general code quality or style.

**autoattend-quality-reviewer** receives:

- The combined diff from the pre-flight check.
- Spec file for context: `.claude/specs/$ARGUMENTS.md`
- Relevant changed source files.
- Instruction: Review only the changed code for quality, maintainability, architecture, and project conventions. Do not comment on security concerns.

Both subagents must run in parallel. Do not wait for one to finish before starting the other.

---

## Step 2: Unified Report

Once both subagents have completed, combine their findings into a single unified report.

De-duplicate overlapping findings. If both agents identify the same code from different perspectives, merge the findings while preserving both perspectives.

Structure the combined report as:

```text
Code Review Report — $ARGUMENTS

Security Findings
[autoattend-security-reviewer output]

Quality Findings
[autoattend-quality-reviewer output]

Combined Action Plan
[Ordered checklist of required improvements,
prioritized by severity]

Overall Verdict
[APPROVED / APPROVED WITH SUGGESTIONS / CHANGES REQUESTED]
```

Prioritize the action plan:

1. Critical/High security findings
2. Important quality changes
3. Medium/Low security findings
4. Quality polish suggestions

Use these verdicts:

```text
APPROVED — ready to commit

APPROVED WITH SUGGESTIONS — can commit; suggestions
can be addressed in future steps

CHANGES REQUESTED — fix the identified issues before committing
```

---

## Step 3: Ask for Approval

After presenting the unified report, ask:

"Do you want me to implement the action plan now?"

Wait for explicit user confirmation before making any changes.

Do not modify files before approval.

---

## Rules

- Do NOT edit any files before user approval.
- Do NOT start one reviewer before the other — both must run in parallel.
- Do NOT skip the pre-flight diff check.
- Do NOT proceed if the feature spec does not exist.
- Do NOT review unrelated unchanged code.
- If either subagent fails or returns no output, report it and do not present a partial review as complete.
- Do NOT invent project requirements when reviewing.
- Keep security and quality responsibilities separate.