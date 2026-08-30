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

/**
 * Register a face sample for many students of one class in ONE request.
 *
 * `files` is a batch of File objects, already sized to fit by
 * utils/fileBatches.js. This function deliberately does not loop: the
 * caller sends batches one after another so it can report progress
 * between them, and an API function that hid several requests behind one
 * call would make that impossible.
 *
 * Note what is *not* sent: no resolved student, and no mapping. Only the
 * files go, each under the repeated part name `images`, and the server
 * matches each file name against the class roster itself — the ID in the
 * name is data to be resolved there, never a claim the client has already
 * settled and the server should trust. That is the whole security
 * property of the feature — a mis-parse here would attribute one
 * person's face to another — so the client never proposes a match, it
 * only displays the one the server made.
 *
 * The file's own name is the payload: it is what the server matches on,
 * so it is passed through untouched rather than renamed the way
 * registerFaceEncoding renames a nameless canvas Blob.
 */
export function importClassFaceEncodings(classId, files) {
  const body = new FormData()
  for (const file of files) body.append('images', file, file.name)

  return request(`/api/classes/${classId}/face-enrollment/import`, {
    method: 'POST',
    body,
  }).then((data) => ({ results: data.results ?? [], summary: data.summary ?? {} }))
}

export function listClassFaceEnrollment(classId) {
  // The response key is `students`: a row is a student plus how many samples
  // they have, not an encoding record.
  return request(`/api/classes/${classId}/face-enrollment`).then(
    (data) => data.students ?? [],
  )
}
