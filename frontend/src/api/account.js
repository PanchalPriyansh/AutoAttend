import { requestJson as request } from './client'

/**
 * Client for the signed-in user's own account.
 *
 * There is deliberately no user id anywhere in this module. The endpoint
 * changes whichever account the auth cookie identifies, so there is
 * nothing here that could be pointed at somebody else -- the same shape
 * as the student attendance client, for the same reason.
 *
 * Cookies, the X-CSRF-TOKEN header, the transparent refresh-and-retry on
 * 401, and turning a backend error body into a thrown Error carrying
 * `.status` are all handled by requestJson in ./client -- none of that is
 * repeated here.
 */
export function changePassword({ currentPassword, newPassword }) {
  return request('/api/auth/password', {
    method: 'POST',
    // The confirmation box is NOT sent. Confirming catches a typo in a
    // control the user cannot read back, which is a property of this
    // form, not of the account -- the server is told the answer once.
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  })
}
