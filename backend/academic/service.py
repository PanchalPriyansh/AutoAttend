"""Business logic for the academic hierarchy: parent-child integrity plus
list/create/update/delete for all five levels.

No Flask request/response objects appear in any signature here -- HTTP
concerns (status codes, JSON bodies) belong to routes/academic.py, which
maps the domain exceptions from academic/errors.py onto status codes.

The five levels share every rule (a child needs a live parent, a parent
cannot be deleted while children reference it, a scoped duplicate is a
conflict), so each operation is written once and driven by the `Level`
descriptors in levels.py.
"""

import logging
from datetime import datetime, timezone

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from academic.errors import DuplicateError, HasChildrenError, NotFoundError

logger = logging.getLogger(__name__)


def _duplicate_message(level):
    """Deliberately vague about *which* field collided.

    Each level has exactly one scoped unique index, but DuplicateKeyError's
    details carry raw index/collection internals that must not reach the
    client (see the "never leak internals" rule in the feature spec).
    """
    scope = f" within this {level.parent.label.lower()}" if level.parent else ""
    return f"A {level.name} with these details already exists{scope}"


def _require_parent_exists(db, level, parent_id):
    if level.parent is None:
        return
    if db[level.parent.collection].count_documents({"_id": parent_id}, limit=1) == 0:
        raise NotFoundError(f"{level.parent.label} not found")


def _require_no_children(db, level, item_id):
    if level.child is None:
        return

    # Counted in full rather than with limit=1 because the count is part of
    # the message the admin sees ("Cannot delete: 3 departments ...").
    count = db[level.child.collection].count_documents({level.child.field: item_id})
    if count:
        noun = level.child.singular if count == 1 else level.child.plural
        verb = "belongs" if count == 1 else "belong"
        raise HasChildrenError(
            f"Cannot delete: {count} {noun} {verb} to this {level.name}"
        )


def list_items(db, level, parent_id=None):
    """Every level except institutes is scoped to its parent, so a list can
    only ever fan out downward -- never across the hierarchy.
    """
    query = {} if level.parent is None else {level.parent.field: parent_id}
    return list(db[level.collection].find(query).sort("name", 1))


def create_item(db, level, parent_id, fields):
    _require_parent_exists(db, level, parent_id)

    document = dict(fields)
    document.update(level.create_defaults)
    if level.parent is not None:
        document[level.parent.field] = parent_id
    # A real datetime, not an ISO string: every collection validator
    # declares created_at as bsonType "date" (see database/schema.py).
    document["created_at"] = datetime.now(timezone.utc)

    try:
        result = db[level.collection].insert_one(document)
    except DuplicateKeyError as exc:
        raise DuplicateError(_duplicate_message(level)) from exc

    document["_id"] = result.inserted_id
    return document


def update_item(db, level, item_id, fields):
    """Updates a document's own fields only.

    `fields` never contains the parent reference -- routes build it from
    the level's own field list -- so a document cannot be re-parented into
    a different branch of the hierarchy.
    """
    try:
        document = db[level.collection].find_one_and_update(
            {"_id": item_id},
            {"$set": fields},
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError as exc:
        raise DuplicateError(_duplicate_message(level)) from exc

    if document is None:
        raise NotFoundError(f"{level.name.capitalize()} not found")

    return document


def delete_item(db, level, item_id):
    """Deletes are blocked, never cascading: silently removing a subtree
    would take its attendance history with it once attendance exists.
    """
    _require_no_children(db, level, item_id)

    # Accepted trade-off: the child check and the delete are not atomic, so
    # a child inserted in between would be orphaned. This is an admin-only
    # screen with no concurrent editors, and a transaction would require a
    # replica set. Revisit if hierarchy editing ever becomes concurrent.
    result = db[level.collection].delete_one({"_id": item_id})
    if result.deleted_count == 0:
        raise NotFoundError(f"{level.name.capitalize()} not found")
