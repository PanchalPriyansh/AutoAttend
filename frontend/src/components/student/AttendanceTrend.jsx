/**
 * Month-by-month attendance, oldest to newest.
 *
 * This carries information a single percentage structurally cannot: 78%
 * climbing and 78% falling are the same number and not the same situation.
 *
 * Built from sized divs. A charting library to draw six rectangles would be a
 * dependency the project carries forever for one screen.
 */

const MONTH_LABELS = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
]

/** "2026-08" -> "Aug 2026", without constructing a Date and inheriting its
 *  timezone behaviour for a value that is only ever a calendar month. */
function describeMonth(month) {
  const [year, index] = month.split('-')
  return `${MONTH_LABELS[Number(index) - 1] ?? month} ${year}`
}

function AttendanceTrend({ months }) {
  if (!months.length) return null

  return (
    <div className="trend">
      <h3 className="trend-heading">Month by month</h3>

      <ol className="trend-columns">
        {months.map((bucket) => (
          <li key={bucket.month} className="trend-column">
            <span className="trend-value">{bucket.percentage}%</span>

            <div
              className="trend-track"
              role="img"
              aria-label={`${describeMonth(bucket.month)}: ${bucket.percentage}% attendance, ${bucket.present_count} of ${bucket.total_count} lectures attended`}
            >
              <div
                className="trend-fill"
                style={{ height: `${bucket.percentage}%` }}
              />
            </div>

            <span className="trend-label">{describeMonth(bucket.month)}</span>
            <span className="trend-count">
              {bucket.present_count}/{bucket.total_count}
            </span>
          </li>
        ))}
      </ol>
    </div>
  )
}

export default AttendanceTrend
