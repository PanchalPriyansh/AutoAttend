import { useEffect, useRef } from 'react'

/**
 * Everything inside the dialog that can take focus. It holds two buttons
 * and nothing else today; the selector is written against the general set
 * so a control added later joins the ring instead of quietly falling out
 * of it.
 */
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), ' +
  'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

/**
 * Confirmation prompt shown before a destructive action, so deleting part
 * of the academic hierarchy is never a single accidental click.
 *
 * Rendered by six files across admin and faculty, which is why it is
 * styled by styles/confirm-dialog.css -- its own stylesheet rather than
 * any one page's. See the note at the top of that file.
 *
 * It declares aria-modal="true", so it owes the keyboard the modality it
 * claims. Tab cycles inside the dialog and cannot reach the page behind
 * the backdrop, and focus is put back where it came from on close. Five
 * of the six callers are guarding a write that cannot be undone, and one
 * of the things Tab used to reach behind the backdrop was the very Delete
 * button that had opened the dialog.
 */
function ConfirmDialog({ open, title, message, confirmLabel = 'Delete', pending, onConfirm, onCancel }) {
  const dialogRef = useRef(null)
  const cancelRef = useRef(null)
  const restoreRef = useRef(null)

  // Focus in on open, and back out on close. Cancel is the target because
  // it is the safe choice, which is also why it comes first in the DOM.
  //
  // The restore is the harder half, and the case it has to survive is the
  // ordinary one rather than an exotic one: every caller closes the dialog
  // on success AND on failure, and in four of the six a successful confirm
  // deletes the row the trigger lived in, so the element we are restoring
  // to is frequently gone by the time we get here. <main> is the fallback
  // -- already focusable for the skip link, and it leaves a keyboard user
  // beside the list they were working in rather than at the top of the
  // document.
  useEffect(() => {
    if (!open) return undefined

    const dialog = dialogRef.current
    restoreRef.current = document.activeElement
    cancelRef.current?.focus()

    return () => {
      const previous = restoreRef.current
      restoreRef.current = null

      // Only take focus back if the dialog still has it. Closing commits
      // the removal before this cleanup runs, so the browser has already
      // dropped focus to <body>; anything else means something outside
      // moved focus deliberately, and it is not ours to take.
      const active = document.activeElement
      if (active && active !== document.body && !dialog?.contains(active)) return

      // Not every platform focuses a button when it is clicked, so the
      // remembered element can be <body> -- which is no more useful to
      // return to than the detached node the other branch guards against.
      if (previous instanceof HTMLElement && previous !== document.body && previous.isConnected) {
        previous.focus()
        return
      }

      document.getElementById('main')?.focus()
    }
  }, [open])

  // A disabled element cannot hold focus, and both buttons are disabled
  // for the length of the request. Browsers answer that by dropping focus
  // to <body> -- outside the dialog, on the page it is covering. Catch it
  // onto the dialog itself, which is what tabIndex={-1} below is for.
  useEffect(() => {
    if (!open || !pending) return

    const dialog = dialogRef.current
    if (dialog && !dialog.contains(document.activeElement)) dialog.focus()
  }, [open, pending])

  useEffect(() => {
    if (!open) return undefined

    function handleKeyDown(event) {
      if (event.key === 'Escape') {
        // Still ignored mid-request: the write is already on its way, and
        // closing would leave nothing on screen saying so.
        if (!pending) onCancel()
        return
      }

      if (event.key !== 'Tab') return

      const dialog = dialogRef.current
      if (!dialog) return

      // Recomputed on every keypress rather than captured on open, because
      // `pending` empties this list mid-dialog.
      const focusable = Array.from(dialog.querySelectorAll(FOCUSABLE))
      const active = document.activeElement

      // Nothing to cycle between: the request is in flight and both
      // buttons are disabled. Hold focus on the dialog rather than let Tab
      // walk out of a prompt the user cannot answer yet.
      if (focusable.length === 0) {
        event.preventDefault()
        dialog.focus()
        return
      }

      const first = focusable[0]
      const last = focusable[focusable.length - 1]

      // Focus is parked on the dialog itself by the branch above, or has
      // left it entirely; either way the next Tab belongs at one end.
      if (active === dialog || !dialog.contains(active)) {
        const target = event.shiftKey ? last : first
        event.preventDefault()
        target.focus()
        return
      }

      if (event.shiftKey && active === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && active === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [open, pending, onCancel])

  if (!open) return null

  return (
    <div className="dialog-backdrop">
      <div
        className="dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        ref={dialogRef}
        tabIndex={-1}
      >
        <h2 id="confirm-title">{title}</h2>
        <p>{message}</p>
        <div className="dialog-actions">
          <button
            type="button"
            className="btn btn--secondary"
            ref={cancelRef}
            onClick={onCancel}
            disabled={pending}
          >
            Cancel
          </button>
          <button
            type="button"
            className="btn btn--danger"
            onClick={onConfirm}
            disabled={pending}
          >
            {pending ? 'Working…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}

export default ConfirmDialog
