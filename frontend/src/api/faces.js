import { requestJson as request } from './client'

/**
 * Client for the admin face-enrolment API.
 *
 * Cookies, the X-CSRF-TOKEN header, the transparent refresh-and-retry on
 * 401, and turning a backend error body into a thrown Error are all handled
 * by requestJson in ./client -- none of that is repeated here.
 *
 * Note what these functions never carry: the encoding vector itself is not
 * part of any response, so there is nothing here that reads or stores one.
 */

export function listStudentFaceEncodings(studentId) {
  return request(`/api/students/${studentId}/face-encodings`).then(
    (data) => data.encodings ?? [],
  )
}

/**
 * `image` is a File or Blob — a file the admin picked, or the Blob the
 * webcam canvas produced. The FormData body is passed through untouched;
 * ./client leaves the Content-Type to the browser so the multipart boundary
 * is set correctly.
 */
export function registerFaceEncoding(studentId, image, source) {
  const body = new FormData()
  // Named "face" rather than the Blob's own name because a canvas capture
  // has none, and the server only reads the part name.
  body.append('image', image, 'face.jpg')
  body.append('source', source)

  return request(`/api/students/${studentId}/face-encodings`, { method: 'POST', body })
}

export function deleteFaceEncoding(studentId, encodingId) {
  return request(`/api/students/${studentId}/face-encodings/${encodingId}`, {
    method: 'DELETE',
  })
}

export function deleteAllFaceEncodings(studentId) {
  return request(`/api/students/${studentId}/face-encodings`, { method: 'DELETE' })
}

export function listClassFaceEnrollment(classId) {
  // The response key is `students`: a row is a student plus how many samples
  // they have, not an encoding record.
  return request(`/api/classes/${classId}/face-enrollment`).then(
    (data) => data.students ?? [],
  )
}
