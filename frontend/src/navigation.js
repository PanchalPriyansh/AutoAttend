/* Where each role can go — written once, here.
 *
 * The header nav and the three landing pages both read this. When the
 * list lived in the pages, the same destination was written out twice
 * under two different names, and nothing would have noticed them
 * disagreeing.
 *
 * This holds application routes and their labels only. It must never
 * hold an institute, department, semester, course, or class — academic
 * data comes from MongoDB.
 *
 * Filtering by role here decides what is *shown*, never what is
 * allowed: every route is guarded by ProtectedRoute in App.jsx and
 * every endpoint by @role_required on the server. Hiding a link a role
 * cannot use is a convenience, not a permission check.
 *
 * Exports data only, no component — a mixed export would add a
 * react(only-export-components) lint warning.
 */

const NAVIGATION = {
  admin: [
    {
      to: '/admin/academics',
      label: 'Academics',
      description:
        'Institute, department, semester, course, and class — and which faculty and students each class holds.',
    },
    {
      to: '/admin/users',
      label: 'Users',
      description:
        'Create accounts, reset passwords, and deactivate people. There is no public sign-up.',
    },
    {
      to: '/admin/face-enrollment',
      label: 'Face Enrollment',
      description:
        "Register each student's face so attendance can recognise them. Photos are never stored.",
    },
  ],
  faculty: [
    {
      to: '/faculty/attendance',
      label: 'Take Attendance',
      description:
        'Capture a class photo or video, review who was recognised, and save the register.',
    },
    {
      to: '/faculty/attendance/history',
      label: 'Attendance History',
      description:
        'Review what has been recorded for your classes, correct a lecture that was marked wrongly, or delete one that should not have been saved.',
    },
  ],
  student: [
    {
      to: '/student/attendance',
      label: 'My Attendance',
      description:
        'See how you stand in each of your classes, how that has moved month by month, and what was recorded for every lecture.',
    },
  ],
}

const HOME = {
  admin: '/admin',
  faculty: '/faculty',
  student: '/student',
}

/* Both tolerate an unknown or missing role rather than throwing: the
 * shell renders during the moment after logout when `user` is already
 * null but ProtectedRoute has not redirected yet. */

export function navigationFor(role) {
  return NAVIGATION[role] ?? []
}

export function homeFor(role) {
  return HOME[role] ?? '/login'
}
