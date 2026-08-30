"""Resolving an uploaded file name to a student on a class roster.

Pure functions -- no Flask, no pymongo, no CV library, no I/O of any
kind. This module decides *who a photo is of*, which makes it the most
consequential rule in bulk import: a wrong answer here attributes one
person's face, and therefore their attendance for as long as the
encoding survives, to another. It is isolated so that rule can be read
and tested on its own.

**The key is the student's ID** -- the roll number a college already
names its photo folders by, `24DCS001.jpg`. There is no `student_id`
field on `users`: `email` is the only unique identifier the collection
has, and the ID is its local part (`24dcs001@charusat.edu.in` ->
`24DCS001`). So the ID is derived from the address rather than stored
beside it, and if a real roll-number field is ever added this is the one
module that has to change.

The rule is deliberately dumb, and the omissions are the design:

  - A stem matches a student's **ID**, case-insensitively. The **full
    email** is also accepted, and is the only way to resolve the
    ambiguous case below; it is not the convention anyone is asked to
    follow. Nothing else matches.
  - No numeric suffix is stripped. `24DCS001-2.jpg` does not match
    `24DCS001`, because `-2` cannot be told apart from a real ID, and a
    wrong strip is a wrong person.
  - Names are not a key at all. Two students may share one; the ID does
    not.
  - Ambiguity is reported, never guessed at. Two students can only
    collide on an ID when their addresses differ by domain, which one
    institute's roster cannot do -- but a deployment spanning two
    domains can, and that is what the full-email form is kept for.

A file name is untrusted text. Nothing here opens it, joins it to a
directory, or otherwise treats it as a path -- the leading directory
segments are discarded on sight (see `stem`), which is a matching fix
that happens to make traversal unreachable as well.
"""

from typing import NamedTuple, Optional

# The extensions that pair with recognition/validators.py's
# ALLOWED_CONTENT_TYPES. The list exists so a trailing extension is
# removed only when it is actually an extension: a blind rsplit on "."
# would turn `aarti.desai` (a local part with a dot in it, and the
# commonest college address shape there is) into `aarti`, matching the
# wrong student or none at all.
IMAGE_EXTENSIONS = ("jpg", "jpeg", "png", "webp")


class Resolution(NamedTuple):
    """One file name's answer.

    `student` is the matched roster document, or None. `match_count` is
    how many roster students the name matched, so the caller can tell the
    two failure modes apart -- 0 is "nobody", 2+ is "several, and this
    module refuses to choose". `student` is populated only when
    `match_count` is exactly 1.
    """

    student: Optional[dict]
    match_count: int


def stem(filename):
    """The comparable key for one uploaded file name.

    Discards any directory segments, drops a trailing image extension,
    trims surrounding whitespace, and lowercases what is left. Returns
    "" for a name with nothing usable in it, which matches nothing.
    """
    if not filename:
        return ""

    # Both separators, because the browser is not the only possible
    # client and a name is never a path here anyway. Taking the last
    # segment cannot change *which* student a name resolves to -- the
    # last segment is the file's own name -- so this is safe as well as
    # necessary.
    name = str(filename).replace("\\", "/").rsplit("/", 1)[-1].strip()

    head, dot, extension = name.rpartition(".")
    if dot and extension.lower() in IMAGE_EXTENSIONS:
        name = head.strip()

    return name.lower()


def student_id(email):
    """The student's ID, taken from the part of their address before the
    domain -- `24dcs001@charusat.edu.in` -> `24dcs001`.

    Split on the *last* `@` so an address whose local part contains one
    keeps it; the domain never does.

    This is the whole of the project's notion of a student ID, and it is
    derived rather than stored: `users` has no roll-number field, only a
    unique `email`. Adding one later means changing this function and
    the index built from it, and nothing else.
    """
    return email.rsplit("@", 1)[0]


def _index_roster(students):
    """Build the two lookups, each key mapping to every student that
    claims it.

    Lists rather than single values even for the email index, which a
    unique index on `users.email` should make impossible to collide: a
    duplicate would otherwise be resolved silently in favour of whichever
    document was iterated last, and reporting it as an ambiguity is the
    behaviour that fails safely.
    """
    by_email = {}
    by_id = {}

    for student in students:
        email = (student.get("email") or "").strip().lower()
        if not email:
            # Unreachable through the API -- email is required on every
            # user -- but a roster row missing one must not become a
            # student that every empty-stemmed file matches.
            continue

        by_email.setdefault(email, []).append(student)
        by_id.setdefault(student_id(email), []).append(student)

    return by_email, by_id


def resolve_roster(filenames, students):
    """Resolve each name in `filenames` against `students`, in order.

    Returns one Resolution per input name, so the caller can zip it back
    onto the files it was given. `students` is the class roster and
    nothing wider: a photo of a student in another class matches nobody
    here, which is what scopes an import to the class it was addressed
    to.

    The expected form is the student's ID; the full email is tried first
    only because the two key spaces cannot overlap -- an ID never
    contains an `@` -- so the order settles nothing in practice and
    exists so that a name given in full is never read as an ID that
    happens to look like one. The email form is what an admin falls back
    to when an ID is ambiguous, which is exactly what that message tells
    them.
    """
    by_email, by_id = _index_roster(students)

    resolutions = []
    for filename in filenames:
        key = stem(filename)
        matches = by_email.get(key) or by_id.get(key) or []
        student = matches[0] if len(matches) == 1 else None
        resolutions.append(Resolution(student, len(matches)))

    return resolutions
