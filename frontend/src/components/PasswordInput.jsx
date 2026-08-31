import { useState } from 'react'

/* A password box with a reveal toggle sitting inside its right edge.
 *
 * It replaces the bare <input type="password"> everywhere somebody
 * types a password they cannot see -- seven boxes on four pages:
 * /login, /forgot-password's reset stage, /account's change-password
 * form, and the two on /admin/users (the new account's password in
 * UserForm, and the reset box on a row). Rendered by more than one
 * page, so it is styled by its own file, styles/password-field.css,
 * rather than by any page's -- the same rule ConfirmDialog,
 * StudentStatusRow and PortalCard already follow.
 *
 * IT IS THE CONTROL, NOT THE FIELD. The label, the hint, the
 * .form-field wrapper and the page's own classes all stay at the call
 * site, exactly as they were written, and the four pages had already
 * spelled those out differently: a --lg field on the two signed-out
 * screens, a page-styled hint on two boxes, .au-field's own flex basis
 * and 280px cap on the reset box, and a <span> rather than a <div>
 * around both admin ones. Swallowing that into a component would have
 * meant a prop per difference. What is shared is the box and the eye,
 * and that is all this owns.
 *
 * The rest of the props are spread straight onto the <input>, so
 * autoComplete, aria-describedby, required and name are written at the
 * call site and read there too. `type` is deliberately NOT among them:
 * it is the one attribute this component exists to control.
 *
 * Revealing is per box and never shared. Two boxes on one form (a new
 * password and its confirmation) toggle independently, which is what
 * makes the pair worth checking against each other at all.
 *
 * The state is deliberately not reset when a form clears, and only one
 * screen can tell: /account empties its three boxes on success while
 * staying on screen, so an eye left open stays open. Everywhere else
 * the question does not arise -- /forgot-password moves to its DONE
 * stage, and both /admin/users forms close on success or cancel, so
 * the box unmounts and comes back hidden.
 *
 * An open eye over an empty box is a visible fact about a control the
 * user set themselves, not a leak, and resetting it would also fire
 * every time somebody selected all and deleted mid-typing -- which is
 * exactly when they are most likely to want to see what they type
 * next.
 *
 * Nothing here is logged, stored, or sent anywhere: the value is the
 * caller's state, and this only decides how it is drawn.
 */
function PasswordInput({ id, className, ...inputProps }) {
  const [visible, setVisible] = useState(false)

  // The wrapper is a <span> made block by CSS, not a <div>. Some of the
  // fields that render this are themselves <span className="form-field">
  // -- that is how /admin/users writes a field -- and a <div> inside a
  // <span> is invalid nesting that browsers only silently tolerate. A
  // span is valid in both places.
  return (
    <span className={className ? `pw-input ${className}` : 'pw-input'}>
      <input
        {...inputProps}
        id={id}
        type={visible ? 'text' : 'password'}
        /* All three matter only while the box is revealed -- a
           type="password" input is already exempt from spellcheck,
           autocorrect and autocapitalisation. A password shown as plain
           text on a phone is not: without these, iOS capitalises its
           first letter and offers to "correct" it, and a desktop
           browser underlines the whole thing in red. */
        spellCheck={false}
        autoCorrect="off"
        autoCapitalize="off"
      />
      {/* Focus is deliberately left on the button after a click rather
          than returned to the box. Returning it would bounce a keyboard
          user straight back out of the control they had just tabbed to,
          and there is nothing to restore for a mouse user, whose caret
          never left. */}
      <button
        type="button"
        className="pw-toggle"
        onClick={() => setVisible((shown) => !shown)}
        /* The name changes rather than carrying aria-pressed alongside
           a fixed one: a toggle should say either what it will do or
           what state it is in, and "Show password, pressed" says both
           at once. aria-controls names the box for the few readers that
           use it. */
        aria-label={visible ? 'Hide password' : 'Show password'}
        aria-controls={id}
      >
        <EyeIcon crossed={visible} />
      </button>
    </span>
  )
}

/* One 24-unit box drawn in currentColor at 20px, so it takes the
 * button's own colour in both palettes and there is no hex here to fall
 * out of step with tokens.css.
 *
 * The eye is what you click to reveal; the crossed eye is what you
 * click to hide again, so the icon always shows what pressing it does.
 *
 * Hidden from screen readers: the button it sits in is already named
 * "Show password" or "Hide password", so the icon would only repeat it.
 */
function EyeIcon({ crossed }) {
  return (
    <svg
      className="pw-icon"
      viewBox="0 0 24 24"
      width="20"
      height="20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M1.8 12S5.9 5.4 12 5.4 22.2 12 22.2 12 18.1 18.6 12 18.6 1.8 12 1.8 12Z" />
      <circle cx="12" cy="12" r="3.1" />
      {crossed && <path d="M4.2 4.2 19.8 19.8" />}
    </svg>
  )
}

export default PasswordInput
