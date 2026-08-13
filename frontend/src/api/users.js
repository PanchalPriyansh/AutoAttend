import { requestJson as request } from './client'

/**
 * Client for the admin user-management API: accounts, faculty assignment,
 * and class enrollment.
 *
 * Cookies, the X-CSRF-TOKEN header, the transparent refresh-and-retry on
 * 401, and turning a backend error body into a thrown Error are all handled
 * by requestJson in ./client -- none of that is repeated here.
 */

export function listUsers({ role, institute_id: instituteId, q } = {}) {
  const params = new URLSearchParams()
  // Empty filters are omitted rather than sent blank: the backend treats a
  // present-but-empty role as absent, but leaving them out keeps the
  // request URL honest about what is actually being filtered on.
  if (role) params.set('role', role)
  if (instituteId) params.set('institute_id', instituteId)
  if (q) params.set('q', q)

  const query = params.toString()
  return request(`/api/users${query ? `?${query}` : ''}`).then((data) => data.users ?? [])
}

export function createUser(fields) {
  return request('/api/users', { method: 'POST', body: JSON.stringify(fields) })
}

export function updateUser(id, fields) {
  return request(`/api/users/${id}`, { method: 'PUT', body: JSON.stringify(fields) })
}

export function setUserStatus(id, isActive) {
  return request(`/api/users/${id}/status`, {
    method: 'PUT',
    body: JSON.stringify({ is_active: isActive }),
  })
}

export function setUserPassword(id, password) {
  return request(`/api/users/${id}/password`, {
    method: 'PUT',
    body: JSON.stringify({ password }),
  })
}

/**
 * `facultyId` of null clears the assignment. The key is always sent: the
 * backend rejects an absent `faculty_id` with a 400 so that a malformed
 * request cannot be read as "unassign".
 */
export function assignFaculty(classId, facultyId) {
  return request(`/api/classes/${classId}/faculty`, {
    method: 'PUT',
    body: JSON.stringify({ faculty_id: facultyId }),
  })
}

export function listClassStudents(classId) {
  // The response key is `enrollments`, not `students`: a row is an
  // enrollment record carrying the student, not the student themselves.
  return request(`/api/classes/${classId}/students`).then((data) => data.enrollments ?? [])
}

export function enrollStudent(classId, studentId) {
  return request(`/api/classes/${classId}/students`, {
    method: 'POST',
    body: JSON.stringify({ student_id: studentId }),
  })
}

export function unenrollStudent(classId, studentId) {
  return request(`/api/classes/${classId}/students/${studentId}`, { method: 'DELETE' })
}
