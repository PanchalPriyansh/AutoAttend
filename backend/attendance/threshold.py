"""The attendance bar, and the arithmetic a student reads against it.

Two separate things live here because they are the same subject seen from
either side: how a percentage is computed from what was recorded, and how
that percentage compares to the percentage the institute requires.

`attendance_percentage` moved here from serializers.py, where 09 first
put it. It is a domain rule, not a serialization concern -- the proof is
that notifications/service.py imports it while serializing nothing -- and
keeping it beside the comparison is what makes "the bar is applied to the
number the student is shown" structural rather than a habit each caller
has to remember. serializers.py imports it from here; the dependency runs
one way.

What this module must never become:

  - **A prediction.** Every figure below is arithmetic on lectures already
    recorded. Nothing here estimates, forecasts, scores, or ranks, and
    there is no model anywhere in this project (see CLAUDE.md).
  - **A verdict.** `meets_threshold` answers "is this figure at or above
    the configured one" and stops. What follows from that is the
    institute's policy, not something a dashboard decides.

Deliberately isolated in two directions:

  - **Nothing from notifications/ is imported here.** That package is
    CLI-only by design so nothing on a request path can send mail, and
    borrowing its settings loader or its MIN_RECORDED_LECTURES would drag
    it onto one through the import graph. Both packages read the same
    Config value independently, which is correct.
  - **No Flask, no pymongo, no CV library, and no database.** Every
    function takes plain numbers and returns plain numbers.
"""

from config import Config

# The range a configured bar has to fall in to mean anything. 0 is not a
# bar (everybody clears it), and above 100 is unreachable by definition.
# Stated here rather than imported from notifications/settings.py, which
# holds the same rule for the sweep: see the isolation note above.
MIN_THRESHOLD = 0
MAX_THRESHOLD = 100

# How far ahead the catch-up figure is worth computing.
#
# Without a cap the answer is technically always yes and practically
# absurd: at a bar of 100, a student with a single absence reaches it only
# once rounding does -- some twenty thousand lectures away -- and printing
# that number would be worse than printing nothing. An answer larger than
# a class holds in a semester is not an answer a student can act on.
#
# A constant rather than a fourth environment variable, for the same
# reason notifications/service.py's MIN_RECORDED_LECTURES is one: it is a
# property of what makes a figure meaningful, not something a deployment
# tunes per institute.
MAX_CATCH_UP_LECTURES = 100


def attendance_percentage(present, total):
    """A student's attendance as a percentage, or None when nothing has
    been recorded yet.

    None rather than 0 is the whole point of this helper existing. A class
    where no lecture has been taken is not a class the student has missed
    everything in, and 0% tells them the exact opposite of the truth. One
    definition, used by the per-class, overall, and monthly figures alike,
    so the empty case cannot be handled three different ways.

    Never stored. The moment a faculty member corrects or deletes a
    session, a stored percentage is wrong and nothing knows it.
    """
    if not total:
        return None

    return round(present / total * 100, 1)


def current_threshold():
    """The configured attendance bar, or None if it is unusable.

    Read from Config directly, the way database/db.py reads MONGODB_URI.

    Never raises, which is the whole difference between this and
    notifications/settings.py::load_sweep_settings. That one refuses to
    run on a bad bar and is right to -- it is about to mail people. A
    dashboard is not: a misconfigured environment variable must not cost a
    student the attendance view they came for. So this degrades to None,
    the two student responses carry `"threshold": null`, and the screen
    shows exactly the attendance it showed before this feature existed.

    A bool is an int in Python, and `True` would silently become a 1% bar.
    Rejected explicitly rather than trusted to the range check.
    """
    threshold = getattr(Config, "LOW_ATTENDANCE_THRESHOLD", None)

    if threshold is None or isinstance(threshold, bool):
        return None
    if not isinstance(threshold, (int, float)):
        return None
    if not MIN_THRESHOLD < threshold <= MAX_THRESHOLD:
        return None

    return float(threshold)


def meets_threshold(percentage, threshold):
    """Whether a recorded percentage is at or above the bar.

    None when either figure is missing, and None is not False: a class
    whose first lecture has not been taken has not failed to meet the
    requirement, and neither has any class when the bar itself is
    unreadable.

    At or above, not strictly above. A student who has exactly met the bar
    has met it, which is the same comparison the sweep makes -- it mails
    only `percentage < threshold` -- so the screen and the email cannot
    disagree about the student sitting exactly on it.
    """
    if percentage is None or threshold is None:
        return None

    return percentage >= threshold


def lectures_to_reach(present, total, threshold):
    """How many further lectures attended in a row would bring this
    student to the bar. 0 if they are already at or above it.

    None when there is nothing to compute from, and None when the answer
    is further away than MAX_CATCH_UP_LECTURES.

    Walked upward rather than solved in closed form on purpose. The
    comparison has to be made against the *rounded* percentage -- the same
    number the student is shown -- so that the figure here and the
    met/below label beside it can never disagree. Solving the unrounded
    ratio is off by one in exactly the case where the student is closest
    to the bar and most likely to be counting.

    This is arithmetic on lectures already recorded, not a projection: it
    answers "how many, from here?", which is a question the student could
    answer with a calculator. It says nothing about what will happen.
    """
    if not total or threshold is None:
        return None

    for further in range(MAX_CATCH_UP_LECTURES + 1):
        percentage = attendance_percentage(present + further, total + further)
        if meets_threshold(percentage, threshold):
            return further

    return None
