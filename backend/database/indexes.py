"""Comparing the indexes a database actually has with the ones schema.py
declares, and deciding what to do about each difference.

Pure data in, pure data out -- no pymongo import, no Flask import, no
I/O. `init_db.py` reads `Collection.index_information()` and hands the
result here; this module only ever looks at dictionaries. That is the
whole reason it is a separate file: the interesting part of index
reconciliation is a judgement about two shapes, and it deserves to be
tested by constructing those shapes by hand rather than by driving a
fake database around them.

Why this exists at all: MongoDB will not change an index in place. Asking
for `{class_id: 1, date: 1}` under a name that already means
`{class_id: 1, date: -1}` raises IndexKeySpecsConflict rather than
updating anything, and because `init_database` walks the collections in
order, that abort leaves every later collection untouched. This project's
own Atlas database drifted exactly that way (see
.claude/specs/20-init-db-index-reconcile.md), and `flask init-db` -- the
first command the README asks a newcomer to run -- could not complete.

The declaration always wins. Where the live database disagrees with
`COLLECTIONS`, the plan is to drop what is there and build what was
declared. What this module will NOT do is form an opinion about an index
nobody declared: `plan_indexes` iterates the declared specs and never the
live ones, so `_id_` and any index an operator added by hand are not
merely skipped, they are never reachable. Tidying those up would be a
different feature with a different risk profile.
"""

__all__ = ["ALLOWED_INFO_KEYS", "ID_INDEX_NAME", "format_keys", "plan_indexes"]


# The index MongoDB creates and maintains itself. It is never declared in
# `schema.py` and can never be dropped, so the key-pattern scan below
# skips it by name rather than relying on no declaration ever being keyed
# on `_id` alone. That is true today, but it is a fact about another
# file; naming it here keeps the invariant local to the code it protects.
ID_INDEX_NAME = "_id_"


# Keys `index_information()` reports that say nothing about how an index
# behaves. Anything outside this set -- `partialFilterExpression`,
# `collation`, `expireAfterSeconds`, `sparse`, a hidden flag -- changes
# what the index is, so its presence on a live index the schema declares
# plainly is a difference, and MongoDB would refuse to reconcile it under
# the same name anyway.
#
# `background` is in the list because it is inert: MongoDB has ignored it
# since 4.2, so an old catalog entry still carrying it describes exactly
# the index the schema declares. Treating it as a difference would drop
# and rebuild a large index to change nothing. `ns` is likewise gone from
# modern listIndexes output and allowed for the same reason; `name` never
# appears here at all, since `index_information()` pops it to use as the
# key -- it is listed to say so, not because it is expected.
ALLOWED_INFO_KEYS = frozenset({"v", "key", "unique", "name", "ns", "background"})


def format_keys(keys):
    """Render a key spec the way MongoDB's own shell writes one.

    This ends up in front of whoever ran the command, so it is worth it
    being the notation they already recognise from `getIndexes()`.
    """
    return "{" + ", ".join(f"{field}: {direction}" for field, direction in keys) + "}"


def _normalise_keys(keys):
    """A list of (field, direction) pairs, comparable by ==.

    Declared keys are already a list of tuples; `index_information()`
    hands back a SON-backed list whose entries compare equal to tuples
    only once they are tuples. Order and direction are both significant
    and are deliberately preserved: `(a, b)` and `(b, a)` are different
    indexes, and so are `1` and `-1`.
    """
    return [(field, direction) for field, direction in keys]


def _describe_declared(index_spec):
    return {
        "name": index_spec["name"],
        "keys": _normalise_keys(index_spec["keys"]),
        "unique": bool(index_spec["unique"]),
    }


def _describe_existing(name, info):
    """One entry from `index_information()` in the same shape.

    `unique` is absent rather than False for a non-unique index, so it is
    coerced here; without that every ordinary index would read as a
    difference against a spec that says `"unique": False`.
    """
    return {
        "name": name,
        "keys": _normalise_keys(info.get("key", [])),
        "unique": bool(info.get("unique", False)),
        "extra_options": sorted(key for key in info if key not in ALLOWED_INFO_KEYS),
    }


def _difference(declared, existing):
    """Why these two cannot be the same index, or None if they can.

    Returns a sentence, not a code: it is printed verbatim, and the point
    of printing it is that the operator can see both sides and judge
    whether the drop is the one they expect.
    """
    if existing["keys"] != declared["keys"]:
        return (
            f"key spec was {format_keys(existing['keys'])}, "
            f"declared {format_keys(declared['keys'])}"
        )

    if existing["unique"] != declared["unique"]:
        return (
            f"unique was {str(existing['unique']).lower()}, "
            f"declared {str(declared['unique']).lower()}"
        )

    if existing["extra_options"]:
        return "carries options not declared: " + ", ".join(existing["extra_options"])

    return None


def plan_indexes(declared_specs, existing_info):
    """What to do about each declared index, given what the database has.

    `declared_specs` is one collection's `indexes` list out of
    `schema.COLLECTIONS`; `existing_info` is that collection's
    `index_information()` (an empty dict for a collection that does not
    exist yet).

    Returns one entry per declared index, in declaration order:

        {"name", "keys", "unique",
         "action": "create" | "keep" | "recreate",
         "drop": [names], "reason": str | None}

    `drop` is a list rather than a single name because the two ways an
    index can conflict are independent and can both be true at once: the
    declared name may exist with the wrong keys *while* some third index
    holds the key pattern the declaration wants. Both have to go before
    the declared index can be built, and a single-name field would
    silently rebuild only half of that.
    """
    existing = {
        name: _describe_existing(name, info) for name, info in existing_info.items()
    }

    plan = []

    for index_spec in declared_specs:
        declared = _describe_declared(index_spec)

        entry = {
            "name": declared["name"],
            "keys": declared["keys"],
            "unique": declared["unique"],
            "action": "create",
            "drop": [],
            "reason": None,
        }

        reasons = []
        same_name = existing.get(declared["name"])

        if same_name is not None:
            difference = _difference(declared, same_name)
            if difference is None:
                # Nothing to do, and nothing else can be in the way: MongoDB
                # would not have allowed a second index with this same key
                # pattern under another name.
                entry["action"] = "keep"
                plan.append(entry)
                continue

            entry["drop"].append(declared["name"])
            reasons.append(difference)

        # An index under a different name holding exactly the declared key
        # pattern blocks the declared name from ever being created --
        # MongoDB rejects a duplicate key pattern under a new name. Sorted
        # so the report reads the same way twice.
        for name in sorted(existing):
            if name == declared["name"] or name == ID_INDEX_NAME:
                continue
            if existing[name]["keys"] == declared["keys"]:
                entry["drop"].append(name)
                reasons.append(f"an index named {name} already has this key pattern")

        if entry["drop"]:
            entry["action"] = "recreate"
            entry["reason"] = "; ".join(reasons)

        plan.append(entry)

    return plan
