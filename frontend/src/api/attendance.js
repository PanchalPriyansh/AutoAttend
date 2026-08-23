import { requestJson as request } from './client'

/**
 * Client for the attendance API — the faculty capture and history screens,
 * and the student's view of their own record.
 *
 * One file because it is one resource. Cookies, the X-CSRF-TOKEN header, the
 * transparent refresh-and-retry on 401, and turning a backend error body into
 * a thrown Error are all handled by requestJson in ./client — none of that is
 * repeated here.
 *
 * Note what these functions never carry: no encoding vector, and no captured
 * image or video beyond the one request that analyses it. Nothing here holds
 * on to a capture after the proposal comes back.
 */

export function listAssignedClasses() {
  return request('/api/classes/assigned').then((data) => data.classes ?? [])
}

/**
 * `capture` is a File or Blob — the file a faculty member picked, or the Blob
 * the webcam canvas produced. `kind` is 'image' or 'video' and decides which
 * part name the backend reads; sending both parts is rejected there, so
 * exactly one is sent here.
 *
 * The FormData body is passed through untouched; ./client leaves the
 * Content-Type to the browser so the multipart boundary is set correctly.
 */
export function recognizeAttendance(classId, capture, kind) {
  const body = new FormData()
  body.append('class_id', classId)
  // Named for the part, not the file: a canvas capture has no filename of its
  // own and the server only reads the part name.
  body.append(kind, capture, kind === 'video' ? 'classroom.webm' : 'classroom.jpg')

  return request('/api/attendance/recognize', { method: 'POST', body })
}

/**
 * Resolves to null when attendance has not been taken yet. A 404 is the
 * expected answer for most dates, so it is turned into an absence of data
 * rather than an error the caller has to special-case at every call site.
 */
export function getAttendanceSession(classId, date) {
  const query = `class_id=${encodeURIComponent(classId)}&date=${encodeURIComponent(date)}`

  return request(`/api/attendance/session?${query}`).catch((error) => {
    if (error.status === 404) return null
    throw error
  })
}

export function saveAttendance({ classId, date, source, records, replace = false }) {
  return request('/api/attendance', {
    method: 'POST',
    body: JSON.stringify({
      class_id: classId,
      date,
      source,
      replace,
      records,
    }),
  })
}

/**
 * One page of the attendance recorded for a class, newest lecture first.
 *
 * `from`/`to` are optional inclusive YYYY-MM-DD bounds. Resolves to the whole
 * body — `{ sessions, total, limit, skip }` — rather than just the array,
 * because the screen needs the unpaged total to page through it and the
 * echoed limit to know whether the one it asked for was clamped.
 */
export function listAttendanceSessions(classId, { from, to, limit, skip } = {}) {
  const query = new URLSearchParams({ class_id: classId })
  if (from) query.set('from', from)
  if (to) query.set('to', to)
  if (limit != null) query.set('limit', String(limit))
  if (skip) query.set('skip', String(skip))

  return request(`/api/attendance/sessions?${query}`)
}

export function getAttendanceSessionById(sessionId) {
  return request(`/api/attendance/sessions/${encodeURIComponent(sessionId)}`)
}

/**
 * Correct a saved session. Only the records travel: the class, the date, who
 * took it, and how it was captured are all properties of the lecture, and
 * correcting who was present does not change any of them.
 */
export function updateAttendanceSession(sessionId, records) {
  return request(`/api/attendance/sessions/${encodeURIComponent(sessionId)}`, {
    method: 'PUT',
    body: JSON.stringify({ records }),
  })
}

export function deleteAttendanceSession(sessionId) {
  return request(`/api/attendance/sessions/${encodeURIComponent(sessionId)}`, {
    method: 'DELETE',
  })
}

/**
 * The signed-in student's own attendance across every class they are enrolled
 * in: `{ classes, overall }`.
 *
 * There is no student id to pass. The backend reads the caller's identity from
 * the session cookie, which is what makes another student's attendance
 * unreachable rather than merely forbidden.
 */
export function getMyAttendance() {
  return request('/api/attendance/me')
}

/**
 * One class in detail for the signed-in student: their standing, a
 * month-by-month trend, and their status for each lecture they were counted
 * in. `from`/`to` are optional inclusive YYYY-MM-DD bounds and narrow the
 * trend and the lecture list together.
 */
export function getMyClassAttendance(classId, { from, to } = {}) {
  const query = new URLSearchParams({ class_id: classId })
  if (from) query.set('from', from)
  if (to) query.set('to', to)

  return request(`/api/attendance/me/sessions?${query}`)
}
