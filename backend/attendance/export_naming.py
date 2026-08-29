"""Naming the file an attendance export hands back.

Pure -- no Flask, no pymongo, no database, and no clock. The generation
date is passed in, so a filename is a function of its arguments and a
test does not have to freeze time to assert one.

One module for both formats because it is one rule, and because that
rule is a security control rather than a cosmetic one. **The slug below
is the `Content-Disposition` header-injection defence.** A class name is
text an admin typed, it reaches an HTTP response header, and the only
reliable way to keep a CR, an LF, a quote, or a semicolon out of that
header is to build the name from a whitelist instead of stripping a
blacklist off it. Nothing outside `[a-z0-9-]` can survive `slugify`,
whatever was in the class name -- so the header cannot be broken by a
character nobody thought to exclude.

Written once rather than in each formatter for the same reason: a second
copy is a second place for the whitelist to be relaxed by somebody who
only had a display problem in mind.
"""

import re

# Every run of characters outside the whitelist collapses to a single
# dash. A whitelist, not a blacklist -- see the module docstring.
_DISALLOWED = re.compile(r"[^a-z0-9]+")

# Long enough for a real class name ("Data Structures - CS-3A" slugs to
# 23 characters), short enough that the slug plus the date range plus the
# extension stays well inside the 255-byte filename limit the operating
# systems this can be saved on impose.
MAX_SLUG_LENGTH = 60

# What a name with nothing whitelistable in it falls back to. A class
# called "***" is unlikely, but a file called "-.csv" is worse than a
# generic one, and an empty slug would produce "attendance--2026-08-29".
FALLBACK_SLUG = "attendance"

# The prefix every exported file carries, so a folder of these sorts
# together and says what they are without being opened.
FILENAME_PREFIX = "attendance"

# How a date is written into a filename. Deliberately the same format
# attendance/serializers.py renders a lecture date in, but declared here
# rather than imported from there: this module is the one both formatters
# and the routes depend on, and it is worth keeping its import list empty
# of everything but `re`.
DATE_FORMAT = "%Y-%m-%d"


def slugify(value):
    """Reduce a class name to the only characters allowed in a filename.

    Lowercased, every other run of characters collapsed to one dash, and
    the leading/trailing dashes trimmed -- so "Data Structures - CS/3A"
    becomes "data-structures-cs-3a".

    Trimmed *after* the length cap as well as before it, so a name cut
    mid-run cannot end on the dash that run collapsed to.
    """
    if not value:
        return FALLBACK_SLUG

    slug = _DISALLOWED.sub("-", str(value).lower()).strip("-")
    slug = slug[:MAX_SLUG_LENGTH].strip("-")

    return slug or FALLBACK_SLUG


def _range_part(date_from, date_to, generated_on):
    """How the file names the range it covers.

    Both bounds given names them both. Anything else names the day the
    file was produced instead of half a range: "attendance-cs-3a-to-
    2026-08-29" reads like a range starting at the beginning of time,
    which is true and useless, and two exports taken a week apart with
    only a `from` would otherwise land on the same filename and silently
    overwrite each other in a downloads folder.
    """
    if date_from is not None and date_to is not None:
        return (
            f"{date_from.strftime(DATE_FORMAT)}-to-{date_to.strftime(DATE_FORMAT)}"
        )

    return generated_on.strftime(DATE_FORMAT)


def export_filename(class_name, *, date_from, date_to, generated_on, extension):
    """The filename for one export: what it is, which class, which range.

    `extension` is "csv" or "pdf" and is not validated against a list --
    it is a literal at both call sites, and a whitelist here would be
    ceremony rather than a defence. It is slugified along with everything
    else, so it cannot smuggle anything into the header either.
    """
    parts = (
        FILENAME_PREFIX,
        slugify(class_name),
        _range_part(date_from, date_to, generated_on),
    )

    return f"{'-'.join(parts)}.{slugify(extension)}"
