import { formatPercentage } from '../../utils/lecture'

/**
 * How one class stands against the attendance a student is required to have,
 * in words.
 *
 * One component rather than a copy on each screen: the class rows and the
 * detail panel say the same thing about the same class, and this is the
 * sentence in this feature least safe to have two versions of. What it may
 * say is constrained (see 11-student-attendance-threshold.md, "Wording"):
 *
 *   - It states a recorded figure against a configured bar, and stops.
 *   - It never projects a final percentage, and never says what will happen.
 *     There is no model behind any of this and no consequence it is entitled
 *     to name — attendance policy belongs to the institute.
 *   - The catch-up line is arithmetic the student could do with a calculator:
 *     how many more, from here. Not a promise that they will get there.
 *
 * Renders nothing when the bar is unreadable or when the class has nothing
 * recorded yet. A requirement stated against no data is noise, and the bar
 * itself already reads "not taken yet".
 */
function ThresholdNote({ threshold, meetsThreshold, lecturesToReach }) {
  if (threshold === null || threshold === undefined) return null
  if (meetsThreshold === null || meetsThreshold === undefined) return null

  const required = `${formatPercentage(threshold)}%`

  // Stated in words, never by colour alone: the class below reads as "Below"
  // to someone who cannot distinguish the two hues, and to a screen reader.
  const standing = meetsThreshold
    ? `Meets the ${required} requirement`
    : `Below the ${required} requirement`

  const catchUp =
    !meetsThreshold && lecturesToReach > 0
      ? lecturesToReach === 1
        ? `Attend the next lecture to reach ${required}`
        : `Attend the next ${lecturesToReach} lectures to reach ${required}`
      : null

  return (
    <span className="threshold-note">
      <span
        className={`threshold-standing threshold-standing--${
          meetsThreshold ? 'met' : 'below'
        }`}
      >
        {standing}
      </span>
      {catchUp ? <span className="threshold-catch-up">{catchUp}</span> : null}
    </span>
  )
}

export default ThresholdNote
