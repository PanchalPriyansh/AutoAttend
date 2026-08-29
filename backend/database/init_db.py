"""Idempotent MongoDB collection/validator/index setup for AutoAttend.

Not run automatically at app startup -- invoked explicitly via the
`flask init-db` CLI command (see app.py) so a temporarily unreachable
MongoDB never prevents the Flask process itself from starting.
"""

from database.indexes import plan_indexes
from database.schema import COLLECTIONS

# How many duplicate key groups to name when a unique index cannot be
# built. Enough to see the shape of the problem; not so many that a badly
# duplicated collection prints itself to a terminal.
DUPLICATE_SAMPLE_LIMIT = 5


def _duplicate_key_values(collection, keys):
    """Key values that appear more than once under this index's fields.

    Read-only, and the reason a unique index is never dropped on faith.
    Dropping one, then failing to rebuild it because the data underneath
    violates it, would leave the collection with no constraint at all --
    strictly worse than the drift being fixed. Asking first turns that
    into a message.

    This is a check, not a guarantee: nothing holds a lock between the
    read here and the drop/create that follows, so a write landing in
    that window could still fail the rebuild. That race is accepted
    rather than closed -- this is an operator-run maintenance command on
    no request path, and the alternative is locking a live collection
    for the length of an index build.

    Only the indexed fields are grouped on and only those come back, so
    nothing here can surface a password hash, an encoding, or the body of
    a notification.
    """
    pipeline = [
        {
            "$group": {
                "_id": {field: f"${field}" for field, _ in keys},
                "count": {"$sum": 1},
            }
        },
        {"$match": {"count": {"$gt": 1}}},
        {"$limit": DUPLICATE_SAMPLE_LIMIT},
    ]

    return [group["_id"] for group in collection.aggregate(pipeline)]


def init_database(db, dry_run=False):
    """Create/update every collection in COLLECTIONS with its validator,
    then make every declared index match its declaration. Safe to call
    repeatedly: `create_collection` is skipped in favor of `collMod` for
    collections that already exist, and an index already matching its
    declaration is left alone.

    Collections are created (with their validator attached) in a separate
    pass before any index work, since creating an index against a
    not-yet-existing collection would implicitly create it with no
    validator attached.

    An index whose live definition has drifted from `schema.py` is
    dropped and rebuilt rather than left to raise. MongoDB will not
    reshape an index in place, so before this the first drift aborted the
    run and every collection after it was skipped -- see
    .claude/specs/20-init-db-index-reconcile.md. Only an index that
    collides with a declared one is ever dropped; nothing reads, let
    alone removes, an index the schema does not declare.

    `dry_run=True` reports exactly what a real run would do while issuing
    no `create_collection`, no `collMod`, no `create_index`, and no
    `drop_index`. It still *reads* -- including the duplicate check --
    because a preview that cannot tell you the real run will be blocked
    is not much of a preview.

    Returns the collection list it always returned, plus an index report
    of what was created, rebuilt, left alone, and blocked. A blocked
    index is reported rather than raised, so one collection with bad data
    does not abandon the other ten; the caller decides what a blocked
    entry means for its exit code.
    """
    existing = set(db.list_collection_names())

    if not dry_run:
        for spec in COLLECTIONS:
            name = spec["name"]
            if name not in existing:
                db.create_collection(name, validator=spec["validator"], validationLevel="strict")
            else:
                db.command("collMod", name, validator=spec["validator"], validationLevel="strict")

    report = {"created": [], "recreated": [], "unchanged": [], "blocked": []}

    for spec in COLLECTIONS:
        name = spec["name"]
        collection = db[name]

        # A collection that did not exist before this run has nothing to
        # report: either it has just been created and holds only `_id_`,
        # or this is a dry run and it does not exist at all. Both plan
        # identically from an empty dict, and neither is worth a round
        # trip -- or, in the dry-run case, a question about a namespace
        # that is not there.
        pre_existing = name in existing
        index_info = collection.index_information() if pre_existing else {}

        for entry in plan_indexes(spec["indexes"], index_info):
            # Only what the report needs. `plan_indexes` is where the full
            # shape of a decision lives -- keys, uniqueness, and which
            # index names have to go -- and a caller wanting those should
            # read it there rather than have them copied through here
            # with nothing consuming them.
            record = {
                "collection": name,
                "name": entry["name"],
                "reason": entry["reason"],
            }

            if entry["action"] == "keep":
                report["unchanged"].append(record)
                continue

            # A collection created moments ago is empty, so there is
            # nothing to be duplicated in it. Everywhere else, ask before
            # building a uniqueness constraint over data that may already
            # violate it -- for a rebuild because the drop must not
            # happen, and for a first creation because the failure would
            # otherwise abort the run mid-pass, which is the very bug
            # this function was changed to stop.
            if entry["unique"] and pre_existing:
                duplicates = _duplicate_key_values(collection, entry["keys"])
                if duplicates:
                    report["blocked"].append({**record, "duplicates": duplicates})
                    continue

            if not dry_run:
                # Check, drop, create -- one index at a time. Dropping
                # every conflict in one pass and creating in another would
                # widen the window in which a failure leaves several
                # collections unconstrained instead of one.
                for index_name in entry["drop"]:
                    collection.drop_index(index_name)

                collection.create_index(
                    entry["keys"],
                    unique=entry["unique"],
                    name=entry["name"],
                )

            report["created" if entry["action"] == "create" else "recreated"].append(record)

    return {
        "collections": [spec["name"] for spec in COLLECTIONS],
        "dry_run": dry_run,
        "indexes": report,
    }
