const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000'

function readCookie(name) {
  const match = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'))
  return match ? decodeURIComponent(match[1]) : null
}

function rawFetch(path, options, csrfCookieName) {
  const method = (options.method || 'GET').toUpperCase()
  const headers = { ...(options.headers || {}) }

  if (options.body !== undefined) {
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
