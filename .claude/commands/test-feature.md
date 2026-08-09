---
description: Writes and runs tests for a specific AutoAttend feature. Pass the spec name as argument e.g. /test-feature 05-attendance-api
allowed-tools: Bash(python -m pytest)
---

Run the full testing pipeline for the feature specified in `$ARGUMENTS`.

If no argument is provided, stop immediately and say:

"Please provide a spec name. Usage: /test-feature <spec-name> e.g. /test-feature 05-attendance-api"

If `.claude/specs/$ARGUMENTS.md` does not exist, stop immediately and say:

"Spec file not found at .claude/specs/$ARGUMENTS.md. Please check the spec name and try again."

---

## Step 1: Write Tests

Invoke the **autoattend-test-writer** subagent with the following context:

- Spec file to base tests on:
  `.claude/specs/$ARGUMENTS.md`
- Relevant source files to read for project structure.
- Output test file(s) appropriate for the feature.
- Instruction: Write tests based on what the spec says the feature SHOULD do. Do NOT derive test logic from the implementation. Cover happy paths, authentication/authorization, validation errors, edge cases, and database/API side effects where applicable.

Wait for autoattend-test-writer to fully complete and confirm the test file(s) have been written before proceeding to Step 2.

---

## Step 2: Run Tests

Once autoattend-test-writer has finished, invoke the **autoattend-test-runner** subagent with the following context:

- Test file(s) created by the test writer.
- Spec file for context:
  `.claude/specs/$ARGUMENTS.md`
- Relevant source files for diagnosing failures.
- Run only the tests created for the specified feature.
- Instruction: Execute the specified tests and analyze failures by cross-referencing the test code, feature specification, and relevant source files. Classify failures as implementation bugs, missing functionality, test/environment problems, or other clearly supported causes.

Do NOT run the full test suite unless explicitly requested.

---

## Handoff Rules

- Do NOT start Step 2 until Step 1 is fully complete.
- Do NOT attempt to fix application code regardless of test results.
- Do NOT run unrelated tests.
- If autoattend-test-writer reports that it could not write the test file(s), stop and report the reason.
- If required test infrastructure or dependencies are unavailable, report the issue rather than silently changing the environment.

---

## Final Output

After both subagents complete, produce a combined summary:

### Testing Pipeline Report — $ARGUMENTS

**Step 1 — Tests Written**

- List each test written with a one-line description of which specification requirement it validates.

**Step 2 — Test Results**

- Mirror the autoattend-test-runner's structured report.

**Verdict**

Use one of:

- ✅ Ready for code review — all tests pass
- ❌ Needs fixes — list the failing tests and their likely root causes
- ⚠️ Test environment issue — tests could not be reliably executed