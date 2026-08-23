/**
 * One mark per lecture, oldest to newest.
 *
 * Shows clustering, which a percentage cannot: four absences in a single week
 * and four scattered across a semester produce an identical figure and mean
 * very different things.
 *
 * `sessions` arrives newest-first, the order the list beneath it is read in.
 * The strip reverses it so that it runs the same direction as the trend above
 * it. Both orderings are deliberate and neither is a bug to be reconciled.
 *
 * Present and absent are distinguished by fill as well as by colour — a
 * red/green-only strip is unreadable to a colourblind student, and this is the
 * one screen in the project a student is guaranteed to open.
 */
function LectureStrip({ sessions }) {
  if (!sessions.length) return null

  const chronological = [...sessions].reverse()

  return (
    <ol className="lecture-strip">
      {chronological.map((lecture) => (
        <li
          key={lecture.date}
          className={`lecture-mark lecture-mark--${lecture.status}`}
          title={`${lecture.date}: ${lecture.status}`}
        >
          <span className="visually-hidden">
            {lecture.date}: {lecture.status}
          </span>
        </li>
      ))}
    </ol>
  )
}

export default LectureStrip
