"""What `flask init-db` says about itself when it finishes.

Kept out of app.py for the reason notifications/reporting.py already
gives: the CLI commands are thin wiring, and presentation does not belong
in the file that builds the app. That module's docstring says `init-db`
"translates a call into a single click.echo" -- true until this command
learned to rebuild a drifted index, which is exactly the event worth
seeing and would be invisible under a one-line summary.

Pure string building. Returns lines rather than printing them, so the
choice of stdout or stderr stays with the command that owns the terminal,
and the report can be asserted directly without a CliRunner, a Flask app,
or a database behind it.

Names and counts, never documents. The one place a value appears is the
duplicate report, and those are the values of the indexed fields alone --
the same fields the index is built on, and nothing else off the document.
"""

__all__ = ["format_index_report"]


def _format_key_values(key_values):
    return ", ".join(f"{field}={value}" for field, value in sorted(key_values.items()))


def _plural(count, singular, plural):
    return singular if count == 1 else plural


def format_index_report(result, dry_run=False):
    """(stdout lines, stderr lines) describing one init_database run.

    A dry run describes itself in the conditional, because nothing it
    lists has happened.
    """
    lines = []
    error_lines = []

    collections = ", ".join(result["collections"])
    lines.append(
        f"{'Would initialize' if dry_run else 'Initialized'} collections: {collections}"
    )

    indexes = result["indexes"]

    for record in indexes["created"]:
        verb = "Would create" if dry_run else "Created"
        lines.append(f"{verb} index {record['collection']}.{record['name']}")

    for record in indexes["recreated"]:
        verb = "Would rebuild" if dry_run else "Rebuilt"
        lines.append(
            f"{verb} index {record['collection']}.{record['name']}: {record['reason']}"
        )

    unchanged = len(indexes["unchanged"])
    lines.append(
        f"{unchanged} {_plural(unchanged, 'index', 'indexes')} already "
        f"{_plural(unchanged, 'matches', 'match')} the schema."
    )

    for record in indexes["blocked"]:
        error_lines.append(
            f"Cannot rebuild {record['collection']}.{record['name']}: the collection "
            f"holds duplicate values for its indexed fields, so a unique index "
            f"cannot be built over it. Nothing was dropped."
        )
        for duplicate in record["duplicates"]:
            error_lines.append(f"  duplicate: {_format_key_values(duplicate)}")

    if indexes["blocked"]:
        error_lines.append(
            "Resolve the duplicate documents above, then run init-db again."
        )

    return lines, error_lines
