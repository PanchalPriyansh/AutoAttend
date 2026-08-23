"""What a sweep says about itself when it finishes.

Kept out of app.py so the CLI command stays the thin wiring the other two
commands are -- `init-db` and `create-admin` each translate a call into a
single `click.echo`, and this feature should not be the one that turns
app.py into a place where presentation lives.

Pure string building, like messages.py and for the same payoff: the
report can be asserted directly, without a CliRunner, a Flask app, or a
database behind it.

Returns lines rather than printing them, so the decision about *where*
output goes -- stdout or stderr -- stays with the command that owns the
terminal. This module has no `click` import and no opinion about it.

Counts, never message bodies. A recipient address can only appear here
because `notify_low_attendance` puts addresses in its result on the dry-
run path alone; on a real run there is nothing in `recipients` for this
to print, whatever it tries.
"""

from notifications.messages import format_percentage

__all__ = ["format_run_report"]


def _describe_recipient(recipient):
    """One would-be recipient and the classes they are short in.

    Dry-run only, by construction -- see the module docstring.
    """
    lines = [f"  {recipient['name']} <{recipient['email']}>"]

    for entry in recipient["classes"]:
        course = entry["course"] or "Unknown course"
        percentage = format_percentage(entry["percentage"])
        lines.append(
            f"    - {course} (Class {entry['class_name']}): {percentage}% "
            f"({entry['present_count']}/{entry['total_count']})"
        )

    return lines


def format_run_report(result, sweep_settings, *, dry_run):
    """Render a completed sweep as `(lines, error_lines)`.

    `error_lines` is separated out rather than merged so the caller can
    send it to stderr: a run that could not reach some students is a
    partial failure, and a partial failure should be visible to a
    scheduled job that is only watching stderr and the exit code.
    """
    threshold = format_percentage(sweep_settings.threshold)
    days = sweep_settings.cooldown_days
    cooldown = "none" if days == 0 else f"{days} day(s)"

    lines = []
    if dry_run:
        lines.append("Dry run: nothing was sent and nothing was recorded.")

    lines.append(f"Threshold: {threshold}%  |  Cooldown: {cooldown}")
    lines.append(f"Below the threshold: {result['candidates']} class(es)")
    lines.append(f"Skipped (notified recently): {result['skipped_cooldown']}")

    verb = "Would notify" if dry_run else "Notified"
    lines.append(
        f"{verb}: {result['students_notified']} student(s) "
        f"about {result['classes_notified']} class(es)"
    )

    for recipient in result["recipients"]:
        lines.extend(_describe_recipient(recipient))

    error_lines = []
    if result["failed"]:
        error_lines.append(
            f"Failed to notify: {result['failed']} student(s). "
            "See server logs for details."
        )

    return lines, error_lines
