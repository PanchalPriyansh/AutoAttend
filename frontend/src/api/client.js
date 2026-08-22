const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000'

function readCookie(name) {
  const match = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'))
  return match ? decodeURIComponent(match[1]) : null
}

function rawFetch(path, options, csrfCookieName) {
  const method = (options.method || 'GET').toUpperCase()
  const headers = { ...(options.headers || {}) }

  // FormData is excluded deliberately: the browser has to set
  // `multipart/form-data; boundary=…` itself, and overriding it with
  // application/json leaves the server unable to parse the parts. Every
  // other body in the project is JSON.
  if (options.body !== undefined && !(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json'
  }

  if (method !== 'GET') {
    const csrfToken = readCookie(csrfCookieName)
    if (csrfToken) headers['X-CSRF-TOKEN'] = csrfToken
  }

  return fetch(`${API_BASE_URL}${path}`, { ...options, method, headers, credentials: 'include' })
}

let refreshInFlight = null

function refreshAccessToken() {
  if (!refreshInFlight) {
    refreshInFlight = rawFetch('/api/auth/refresh', { method: 'POST' }, 'csrf_refresh_token').finally(() => {
      refreshInFlight = null
    })
  }
  return refreshInFlight
}

/**
 * Fetch wrapper for the AutoAttend API: always sends cookies, attaches the
 * CSRF header on mutating requests, and transparently refreshes an expired
 * access token once before retrying on a 401.
 */
export async function apiFetch(path, options = {}) {
  let response = await rawFetch(path, options, 'csrf_access_token')

  const isAuthEndpoint = path === '/api/auth/refresh' || path === '/api/auth/login'
  if (response.status === 401 && !isAuthEndpoint) {
    const refreshResponse = await refreshAccessToken()
    if (refreshResponse.ok) {
      response = await rawFetch(path, options, 'csrf_access_token')
    }
  }

  return response
}

/**
 * apiFetch plus JSON unwrapping: returns the parsed body, or throws an Error
 * carrying the backend's own `error` message on a non-OK response. Components
 * render that message directly, so a 409 reaches the user as "Cannot delete: 3
 * departments belong to this institute" rather than a generic failure.
 *
 * The thrown Error also carries `.status`, for the few cases where the code
 * changes what the UI *does* rather than only what it says: attendance treats
 * a 404 as "not taken yet" instead of a failure, and a 409 as an offer to
 * replace. Matching on the message text instead would break the moment a
 * backend message is reworded.
 */
export async function requestJson(path, options = {}) {
  const response = await apiFetch(path, options)
  const data = await response.json().catch(() => ({}))

  if (!response.ok) {
    const error = new Error(data.error || 'Request failed')
    error.status = response.status
    throw error
  }

  return data
}
