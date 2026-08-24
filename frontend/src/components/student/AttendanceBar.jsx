import { formatPercentage } from '../../utils/lecture'

/**
 * A horizontal attendance bar with its figure beside it.
 *
 * Used at two scopes — the student's overall standing and each individual
 * class — because they are the same measurement and a second copy would drift
 * the first time one of them handled the empty case differently.
 *
 * `percentage` is null when nothing has been recorded yet. That is not zero,
 * and it must not be drawn as an empty bar: a class whose first lecture has
 * not happened is not a class the student has missed everything in.
 *
 * `threshold` is optional and marks the attendance the student is required to
 * have. It is passed for a class and deliberately not for the overall bar: the
 * bar is defined per class, and an average hides the one class a student is
 * actually short in. Absent, this renders exactly what it always rendered.
 */
function AttendanceBar({
  percentage,
  presentCount,
  totalCount,
  threshold = null,
  size = 'normal',
}) {
  const recorded = percentage !== null && percentage !== undefined
  const marked = threshold !== null && threshold !== undefined

  // Named in the label as well as drawn, so the requirement is not information
  // available only to someone who can see the line.
  const label = marked
    ? `${percentage}% attendance, ${presentCount} of ${totalCount} lectures attended, ${formatPercentage(threshold)}% required`
    : `${percentage}% attendance, ${presentCount} of ${totalCount} lectures attended`

  return (
    <div className={`attendance-bar attendance-bar--${size}`}>
      <div className="attendance-bar-figures">
        <span className="attendance-bar-percent">
          {recorded ? `${percentage}%` : 'Not taken yet'}
        </span>
        <span className="attendance-bar-count">
          {recorded
            ? `${presentCount} of ${totalCount} lectures`
            : 'No attendance recorded for this class'}
        </span>
      </div>

      {/* The track is omitted rather than drawn empty when there is nothing to
          measure — an empty bar reads as 0%, which is the opposite of the
          truth. Width is the one value that has to be inline; everything else
          is in index.css so it can be themed. */}
      {recorded ? (
        <div className="attendance-bar-track" role="img" aria-label={label}>
          <div className="attendance-bar-fill" style={{ width: `${percentage}%` }} />
          {/* Decorative: the requirement reaches assistive technology through
              the label above and the note beside the bar, so announcing the
              mark a third time would only add noise. Its offset is the one
              value that has to be inline. */}
          {marked ? (
            <div
              className="attendance-bar-marker"
              style={{ left: `${threshold}%` }}
              aria-hidden="true"
            />
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

export default AttendanceBar
