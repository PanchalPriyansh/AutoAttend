/**
 * Small helpers shared by the faculty attendance screens.
 *
 * Extracted from AttendanceCapture.jsx when the history screen needed the
 * same two things. `today()` in particular has a correctness reason for
 * existing that a second, independently written copy would almost certainly
 * get wrong.
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
 * A class as a faculty member reads it: the course it belongs to and the
 * class's own name. The rest of the hierarchy is shown separately, once, for
 * whichever class is selected.
 */
export function describeClass(item) {
  return [item.course, item.name].filter(Boolean).join(' — ')
}
