import { useEffect, useRef, useState } from 'react'
import PasswordInput from '../PasswordInput'

const ROLES = ['admin', 'faculty', 'student']

function initialValues(user) {
  return {
    name: user?.name ?? '',
    email: user?.email ?? '',
    password: '',
    role: user?.role ?? 'student',
    institute_id: user?.institute_id ?? '',
  }
}

/**
 * Create/edit form for a user account. One component for both modes because
 * they share every field they have in common; the differences are exactly
 * the two backend rules:
 *
 *   - role is immutable after creation, so edit mode shows it as text
 *   - passwords are set through their own endpoint, so edit mode has no
 *     password field
 *
 * Showing an editable role or a password box in edit mode would imply the
 * API accepts changes it silently ignores.
 */
function UserForm({ mode, user, institutes, onSubmit, onCancel }) {
  const [values, setValues] = useState(() => initialValues(user))
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const errorRef = useRef(null)

  // The backend answers with one message for the whole form ("Email
  // already registered"), not a per-field one, so there is no first
  // field to send focus to -- the message itself is the target. It is
  // focused rather than merely announced so a keyboard user is taken to
  // it instead of being left on a Save button that appeared to do
  // nothing. Runs after render, because the node does not exist at the
  // moment setError is called.
  useEffect(() => {
    if (error) errorRef.current?.focus()
  }, [error])

  const isCreate = mode === 'create'

  function setField(field, value) {
    setValues((current) => ({ ...current, [field]: value }))
  }

  async function handleSubmit(event) {
    event.preventDefault()
    setError('')
    setSubmitting(true)
    try {
      const payload = {
        name: values.name,
        email: values.email,
        institute_id: values.institute_id || null,
      }
      if (isCreate) {
        payload.password = values.password
        payload.role = values.role
      }
      await onSubmit(payload)
    } catch (err) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  const idPrefix = isCreate ? 'new-user' : `edit-user-${user.id}`

  return (
    <form
      className={isCreate ? 'au-form au-form--create' : 'au-form au-form--edit'}
      onSubmit={handleSubmit}
    >
      <span className="form-field au-field">
        <label htmlFor={`${idPrefix}-name`}>Name</label>
        <input
          id={`${idPrefix}-name`}
          name="name"
          type="text"
          autoComplete="off"
          value={values.name}
          onChange={(event) => setField('name', event.target.value)}
          required
        />
      </span>

      <span className="form-field au-field">
        <label htmlFor={`${idPrefix}-email`}>Email</label>
        <input
          id={`${idPrefix}-email`}
          name="email"
          type="email"
          spellCheck={false}
          value={values.email}
          onChange={(event) => setField('email', event.target.value)}
          autoComplete="off"
          required
        />
      </span>

      {isCreate && (
        <span className="form-field au-field">
          <label htmlFor={`${idPrefix}-password`}>Password</label>
          <PasswordInput
            id={`${idPrefix}-password`}
            name="password"
            value={values.password}
            onChange={(event) => setField('password', event.target.value)}
            autoComplete="new-password"
            required
          />
        </span>
      )}

      <span className="form-field au-field">
        <label htmlFor={`${idPrefix}-role`}>Role</label>
        {isCreate ? (
          <select
            id={`${idPrefix}-role`}
            name="role"
            value={values.role}
            onChange={(event) => setField('role', event.target.value)}
          >
            {ROLES.map((role) => (
              <option key={role} value={role}>
                {role}
              </option>
            ))}
          </select>
        ) : (
          <input id={`${idPrefix}-role`} name="role" type="text" value={values.role} readOnly />
        )}
      </span>

      <span className="form-field au-field">
        <label htmlFor={`${idPrefix}-institute`}>Institute</label>
        <select
          id={`${idPrefix}-institute`}
          name="institute_id"
          value={values.institute_id}
          onChange={(event) => setField('institute_id', event.target.value)}
        >
          <option value="">— None —</option>
          {institutes.map((institute) => (
            <option key={institute.id} value={institute.id}>
              {institute.name}
            </option>
          ))}
        </select>
      </span>

      <button type="submit" className="btn btn--primary au-submit" disabled={submitting}>
        {submitting ? 'Saving…' : 'Save'}
      </button>
      <button
        type="button"
        className="btn btn--secondary au-submit"
        onClick={onCancel}
        disabled={submitting}
      >
        Cancel
      </button>

      {error && (
        <p ref={errorRef} tabIndex={-1} role="alert" className="callout callout--error au-alert">
          <span className="callout-mark" aria-hidden="true">
            !
          </span>
          {error}
        </p>
      )}
    </form>
  )
}

export default UserForm
