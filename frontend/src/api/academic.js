import { requestJson as request } from './client'

/**
 * Builds the client for one hierarchy level. `parentParam` is the query
 * parameter/body field naming its parent, or null for institutes, which
 * sit at the top of the hierarchy and are the only unscoped level.
 */
function resource(plural, parentParam) {
  const base = `/api/${plural}`

  return {
    plural,
    parentParam,

    async list(parentId) {
      const query = parentParam ? `?${parentParam}=${encodeURIComponent(parentId)}` : ''
      const data = await request(`${base}${query}`)
      return data[plural] ?? []
    },

    create(parentId, fields) {
      const body = parentParam ? { [parentParam]: parentId, ...fields } : fields
      return request(base, { method: 'POST', body: JSON.stringify(body) })
    },

    update(id, fields) {
      return request(`${base}/${id}`, { method: 'PUT', body: JSON.stringify(fields) })
    },

    remove(id) {
      return request(`${base}/${id}`, { method: 'DELETE' })
    },
  }
}

export const institutes = resource('institutes', null)
export const departments = resource('departments', 'institute_id')
export const semesters = resource('semesters', 'department_id')
export const courses = resource('courses', 'semester_id')
export const classes = resource('classes', 'course_id')
