import { requestJson as request } from './client'

/**
 * Client for the signed-out password reset flow.
 *
 * A separate module from ./account, which is explicitly the *signed-in*
 * user's own account: nothing here runs with a session, and nothing here
 * establishes one. A successful reset returns a message and no cookie —
 * the user then logs in normally with the password they just chose.
 *
 * There is no user id in this module and no token. The only things it
 * sends are an address, a code from an inbox, and a new password.
 *
 * Cookies, the X-CSRF-TOKEN header, and turning a backend error body into
 * a thrown Error carrying `.status` are all handled by requestJson in
 * ./client. The transparent refresh-and-retry it also does never fires
 * here: both endpoints answer 200 or 400, never 401, which is deliberate
 * — a retried 401 would resubmit the code and burn a second of the five
 * attempts it is allowed.
 */

/**
 * Ask for a code. Resolves the same way whether or not the address has an
 * account, so nothing the caller can observe reveals which — do not add a
 * branch here that tries to tell them apart.
 */
export function requestPasswordReset({ email }) {
  return request('/api/auth/forgot-password', {
    method: 'POST',
    body: JSON.stringify({ email }),
  })
}

/**
 * Spend the code and set the new password.
 *
 * The confirmation box is NOT sent, the same call ./account makes:
 * confirming catches a typo in a control the user cannot read back, which
 * is a property of the form rather than of the account.
 */
export function resetPassword({ email, code, newPassword }) {
  return request('/api/auth/reset-password', {
    method: 'POST',
    body: JSON.stringify({ email, code, new_password: newPassword }),
  })
}
