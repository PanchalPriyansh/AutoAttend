import { useEffect, useRef, useState } from 'react'
import { changePassword } from '../api/account'
import PasswordInput from '../components/PasswordInput'
import AppShell from '../components/layout/AppShell'
import { useAuth } from '../context/AuthContext'

const EMPTY = { current: '', next: '', confirm: '' }

/* The one page every role shares.
 *
 * It shows who you are signed in as and lets you change your own
 * password, and that is all it does. Name, email and role are shown as
 * read-only facts because the API genuinely will not change them here --
 * rendering them as inputs would imply an endpoint that does not exist.
 *
 * It is not in navigation.js and must not be added to it: that file is
 * read by the header nav AND all three landing-page grids, so a link
 * there appears on six surfaces, takes the student nav off
 * .app-nav--single, and invalidates the 413px collapse breakpoint that
 * 17 derived from faculty's link list. This page is reached from the
 * user's own name in the header, beside Log out, where the
 * identity-scoped controls already are.
 */
function Account() {
  const { user } = useAuth()

  const [values, setValues] = useState(EMPTY)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const errorRef = useRef(null)

  // Focused rather than merely announced, the same call UserForm makes:
  // the backend answers with one message for the whole form, so the
  // message itself is the target, and a keyboard user is taken to it
  // instead of being left on a button that appeared to do nothing.
  useEffect(() => {
    if (error) errorRef.current?.focus()
  }, [error])

  function update(field) {
    return (event) => {
      const { value } = event.target
      setValues((previous) => ({ ...previous, [field]: value }))
    }
  }

  async function handleSubmit(event) {
    event.preventDefault()
    setError('')
    setSuccess('')

    // The only rule this form enforces on its own, and it is enforced
    // before any request is made -- a mismatch is a typo, not something
    // the server should be asked about.
    if (values.next !== values.confirm) {
      setError('The new passwords do not match.')
      return
    }

    setSubmitting(true)
    try {
      const result = await changePassword({
        currentPassword: values.current,
        newPassword: values.next,
      })
      // Cleared only on success. A failed attempt leaves what was typed
      // in place, so a wrong current password costs one field, not three.
      setValues(EMPTY)
      setSuccess(result?.message || 'Password changed')
    } catch (err) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <AppShell title="Account">
      <div className="account">
        <section className="card account-panel">
          <h2 className="account-heading">Your details</h2>
          <dl className="account-facts">
            <div className="account-fact">
              <dt>Name</dt>
              <dd>{user?.name}</dd>
            </div>
            <div className="account-fact">
              <dt>Email</dt>
              <dd className="account-email">{user?.email}</dd>
            </div>
            <div className="account-fact">
              <dt>Role</dt>
              <dd className="account-role">{user?.role}</dd>
            </div>
          </dl>
          <p className="account-note">
            These are set by your college administrator. AutoAttend has no public
            sign-up, so they cannot be changed here.
          </p>
        </section>

        <section className="card account-panel">
          <h2 className="account-heading">Change password</h2>
          <form className="account-form" onSubmit={handleSubmit}>
            <div className="form-field">
              <label htmlFor="current-password">Current password</label>
              <PasswordInput
                id="current-password"
                name="current_password"
                value={values.current}
                onChange={update('current')}
                autoComplete="current-password"
                required
              />
            </div>

            <div className="form-field">
              <label htmlFor="new-password">New password</label>
              <PasswordInput
                id="new-password"
                name="new_password"
                value={values.next}
                onChange={update('next')}
                autoComplete="new-password"
                aria-describedby="new-password-hint"
                required
              />
              {/* Mirrors MIN_PASSWORD_LENGTH in users/validators.py. The
                  server is the authority and names the number itself when
                  it refuses; this is only so the rule is known before
                  the attempt. */}
              <p id="new-password-hint" className="account-hint">
                Use at least 8 characters.
              </p>
            </div>

            <div className="form-field">
              <label htmlFor="confirm-password">Confirm new password</label>
              <PasswordInput
                id="confirm-password"
                name="confirm_password"
                value={values.confirm}
                onChange={update('confirm')}
                autoComplete="new-password"
                required
              />
            </div>

            {error && (
              <p
                ref={errorRef}
                tabIndex={-1}
                role="alert"
                className="callout callout--error account-alert"
              >
                <span className="callout-mark" aria-hidden="true">
                  !
                </span>
                {error}
              </p>
            )}

            {/* role="alert" already announces the error above; success is
                not an interruption, so it gets a polite region instead.
                The region is rendered whether or not it holds anything,
                because a live region added to the page at the same moment
                as its text is not reliably announced. */}
            <div aria-live="polite">
              {success && (
                <p className="callout callout--success account-alert">
                  <span className="callout-mark" aria-hidden="true">
                    ✓
                  </span>
                  {success}
                </p>
              )}
            </div>

            <button
              type="submit"
              className="btn btn--primary account-submit"
              disabled={submitting}
            >
              {submitting ? 'Changing…' : 'Change password'}
            </button>
          </form>

          {/* Written from the observed behaviour, not from the backend's
              intent. Verified in a browser: the changing session keeps
              working (the response hands it a replacement refresh
              cookie), a second session signed in beforehand is refused
              at its very next refresh, and what survives in that second
              session is only its unexpired access token -- at most
              JWT_ACCESS_TOKEN_EXPIRES, 15 minutes.

              The old wording said the other session "can keep working
              for up to 15 minutes", which described the access token
              and quietly implied the session as a whole. Before 24 that
              was wrong by a week; it is now merely the smaller half of
              the truth, so it leads with the outcome instead. */}
          <p className="account-note">
            You stay signed in on this device. Anywhere else you are signed in
            is signed out, though a device that is already open can keep
            working for up to 15 minutes.
          </p>
        </section>
      </div>
    </AppShell>
  )
}

export default Account
