import { requestJson as request } from './client'

/**
 * Client for the faculty attendance-capture API.
 *
 * Cookies, the X-CSRF-TOKEN header, the transparent refresh-and-retry on
 * 401, and turning a backend error body into a thrown Error are all handled
 * by requestJson in ./client — none of that is repeated here.
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
