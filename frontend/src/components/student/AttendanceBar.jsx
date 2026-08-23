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
 */
function AttendanceBar({ percentage, presentCount, totalCount, size = 'normal' }) {
  const recorded = percentage !== null && percentage !== undefined

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
        <div
          className="attendance-bar-track"
          role="img"
          aria-label={`${percentage}% attendance, ${presentCount} of ${totalCount} lectures attended`}
        >
          <div className="attendance-bar-fill" style={{ width: `${percentage}%` }} />
        </div>
      ) : null}
    </div>
  )
}

export default AttendanceBar
