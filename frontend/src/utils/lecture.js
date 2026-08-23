/**
 * Small helpers shared by the attendance screens.
 *
 * Extracted from AttendanceCapture.jsx when the history screen needed the
 * same two things, and shared with the student dashboard since. `today()` in
 * particular has a correctness reason for existing that a second,
 * independently written copy would almost certainly get wrong.
 */

/**
 * Today as the faculty member's own calendar day, not the UTC one. Building
 * it from toISOString() would show yesterday's date to anyone east of
 * Greenwich for the first hours of their morning.
 */
export function today() {
  const now = new Date()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${now.getFullYear()}-${month}-${day}`
}

/**
 * A class as a person reads it: the course it belongs to and the class's own
 * name. The rest of the hierarchy is shown separately, once, for whichever
 * class is selected. Used by the faculty screens for an assigned class and by
 * the student dashboard for an enrolled one — the same label either way, which
 * is why there is one of these and not two.
 */
export function describeClass(item) {
  return [item.course, item.name].filter(Boolean).join(' — ')
}
