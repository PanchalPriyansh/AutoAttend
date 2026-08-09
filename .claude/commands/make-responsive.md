---
description: Reviews AutoAttend frontend responsiveness only when significant UI changes have occurred since the previous responsive review.
---

Run a responsive-design review of the AutoAttend frontend using the `autoattend-responsive-designer` agent.

Before making any changes, inspect the project history and current frontend state to determine whether significant UI/frontend changes have occurred since the previous responsive review.

Significant changes include:

- New pages or screens
- Major page/layout changes
- New dashboards or dashboard sections
- New forms or complex components
- New or substantially changed tables
- Navigation/sidebar changes
- Camera or face-recognition UI changes
- Major changes to shared UI components

Do not treat backend-only changes, database changes, API-only changes, documentation, tests, text-only changes, or minor styling changes as significant UI changes.

### If there are no significant UI changes

Stop without modifying any files.

Inform the user:

"No responsive review is needed right now. No significant frontend/UI changes have been made since the previous responsive review."

Do not perform unnecessary responsive checks or changes.

### If significant UI changes exist

Invoke the `autoattend-responsive-designer` agent.

The agent should:

1. Identify the affected frontend pages and shared components.
2. Check desktop, laptop, tablet, and mobile layouts.
3. Identify responsive issues.
4. Fix confirmed issues.
5. Re-check the affected UI.
6. Avoid modifying unrelated functionality.

At the end, report:

- Significant UI changes detected
- Pages/components reviewed
- Responsive issues found
- Changes made
- Any remaining issues

Do not create a feature specification.

Do not run the normal feature implementation workflow.