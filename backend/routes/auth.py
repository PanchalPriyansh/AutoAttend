import logging

from flask import Blueprint, jsonify, request
from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
    get_jwt,
    get_jwt_identity,
    jwt_required,
    set_access_cookies,
    set_refresh_cookies,
    unset_jwt_cookies,
)
from pymongo.errors import PyMongoError

from auth.errors import (
    InactiveAccountError,
    IncorrectPasswordError,
    InvalidResetCodeError,
)
from auth.password_change import change_password
from auth.password_reset import issue_reset_code, reset_password_with_code
from auth.reset_codes import CODE_TTL_MINUTES, MAX_SUBMITTED_CODE_LENGTH
from auth.service import authenticate_user, get_user_by_id, to_safe_profile
from auth.tokens import is_token_current, refresh_claims_for
from common.errors import NotFoundError, ValidationError
from common.validators import require_bounded_string
from config import Config
from database.db import get_db
from notifications.errors import MailerNotConfiguredError, MailerSendError
from notifications.mailer import SmtpTransport, build_message
from notifications.reset_messages import build_reset_body, build_reset_subject
from notifications.settings import load_smtp_settings
from users.validators import require_email, require_existing_password, require_password

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")


@auth_bp.errorhandler(PyMongoError)
@auth_bp.errorhandler(RuntimeError)
def _handle_database_error(exc):
    """Keeps auth responses on the JSON error contract instead of falling
    through to Flask's default HTML/traceback handler when MongoDB is
    unreachable or misconfigured (RuntimeError is raised by get_db() when
    MONGODB_URI is unset).
    """
    logger.exception("Database error in an auth route")
    return jsonify({"error": "Service temporarily unavailable"}), 500


@auth_bp.errorhandler(ValidationError)
def _handle_validation_error(exc):
    return jsonify({"error": str(exc)}), 400


@auth_bp.errorhandler(NotFoundError)
def _handle_not_found_error(exc):
    """Insurance, not a live path -- and deliberately not part of
    /api/auth/password's documented status codes.

    Nothing in this blueprint can currently raise a bare NotFoundError:
    the one source is set_user_password, and auth/password_change.py
    catches it and re-raises InactiveAccountError, because the answer to
    "your account no longer exists" is authenticate-again rather than a
    missing-resource page. This handler exists so that a LATER route
    added to auth_bp cannot fall through to Flask's default HTML
    traceback instead of the JSON error contract. 404 matches how every
    other blueprint in the project maps this exception, which is the
    right default for a route that has not been written yet.
    """
    return jsonify({"error": str(exc)}), 404


@auth_bp.errorhandler(IncorrectPasswordError)
def _handle_incorrect_password_error(exc):
    """403 rather than 401 -- see the class docstring in auth/errors.py.
    A 401 would be transparently retried by the frontend's apiFetch,
    submitting the same wrong password twice per attempt.
    """
    return jsonify({"error": str(exc)}), 403


@auth_bp.errorhandler(InactiveAccountError)
def _handle_inactive_account_error(exc):
    return jsonify({"error": str(exc)}), 401


@auth_bp.errorhandler(InvalidResetCodeError)
def _handle_invalid_reset_code_error(exc):
    """400 rather than 401 -- see the class docstring in auth/errors.py.
    A 401 would be transparently retried by the frontend's apiFetch,
    resubmitting the code and burning a second of the five attempts it is
    allowed.
    """
    return jsonify({"error": str(exc)}), 400


@auth_bp.errorhandler(MailerNotConfiguredError)
def _handle_mailer_not_configured_error(exc):
    """A deployment that cannot send mail, reported as unavailable.

    The exception's own message names the setting that is missing, which
    is right on an operator's terminal and wrong in an HTTP body: this
    endpoint is reachable by anyone, signed out. So the message is logged
    and a generic one is returned. notifications/settings.py guarantees
    these messages name a setting and never its value, so logging one
    cannot write a credential to a log file.

    This is the one status /api/auth/forgot-password can return that is
    not the generic 200 -- and it is safe precisely because it does not
    depend on the address: load_smtp_settings runs before any user
    lookup, so a misconfigured server answers this to everybody alike
    rather than only to addresses that exist.
    """
    logger.error("Password reset is unavailable: %s", exc)
    return jsonify({"error": "Password reset is temporarily unavailable"}), 503


@auth_bp.route("/login", methods=["POST"])
def login():
    body = request.get_json(silent=True) or {}
    email = body.get("email")
    password = body.get("password")

    if not isinstance(email, str) or not isinstance(password, str) or not email or not password:
        return jsonify({"error": "email and password are required"}), 400

    user = authenticate_user(get_db(), email, password)
    if user is None:
        return jsonify({"error": "Invalid email or password"}), 401

    identity = str(user["_id"])
    access_token = create_access_token(identity=identity, additional_claims={"role": user["role"]})
    # The refresh token is stamped with the password version current at
    # login; /refresh compares the stamp to the document, so this session
    # dies the moment the password behind it is replaced. The ACCESS
    # token deliberately carries no such claim -- nothing reads one, and
    # a claim nobody checks looks like a guarantee it is not.
    refresh_token = create_refresh_token(
        identity=identity, additional_claims=refresh_claims_for(user)
    )

    response = jsonify(to_safe_profile(user))
    set_access_cookies(response, access_token)
    set_refresh_cookies(response, refresh_token)
    return response, 200


@auth_bp.route("/refresh", methods=["POST"])
@jwt_required(refresh=True)
def refresh():
    """Mint a new access token, if the session behind this one still stands.

    Three ways it does not, and they are answered identically on purpose:
    the account is gone, the account is deactivated, or the password the
    session was established with has since been replaced. A caller
    holding stolen cookies must not be able to tell which -- the refusal
    would otherwise report whether an account still exists and whether
    somebody has just changed their password.

    The version check is what makes the 15 minutes this project quotes
    elsewhere true. Before it, refresh asked only about `is_active`, so a
    refresh cookie -- which lives seven days -- kept minting access
    tokens for a week after the owner changed their password.
    """
    identity = get_jwt_identity()
    user = get_user_by_id(get_db(), identity)
    if (
        user is None
        or not user.get("is_active", False)
        or not is_token_current(get_jwt(), user)
    ):
        return jsonify({"error": "Authentication required"}), 401

    access_token = create_access_token(identity=identity, additional_claims={"role": user["role"]})
    response = jsonify({"refreshed": True})
    set_access_cookies(response, access_token)
    return response, 200


@auth_bp.route("/logout", methods=["POST"])
@jwt_required()
def logout():
    response = jsonify({"message": "Logged out"})
    unset_jwt_cookies(response)
    return response, 200


@auth_bp.route("/password", methods=["POST"])
@jwt_required()
def change_own_password():
    """Change the signed-in user's own password.

    Any signed-in role, with no role check at all: an admin, a faculty
    member and a student have exactly equal standing over their own
    credential.

    No user id is accepted anywhere -- not in the path, not in the body.
    The account changed is the one the token identifies, so there is no id
    for a caller to tamper with, and any id-shaped field in the body is
    simply never read.

    Nothing is logged here. A failed attempt carries the submitted
    password in the caller's hand, and the surest way never to write one
    to a log file is to have no log statement on the path at all.

    A successful change ends every OTHER session on the account, and
    keeps this one. The write bumps `token_version`, which strands every
    refresh token minted under the old password -- including this
    caller's -- so the response hands this session a replacement. Without
    it, 23's promise that you stay signed in on this device would hold
    for fifteen minutes and then drop the user at /login for the crime of
    changing their password.

    Only the refresh cookie is replaced. The access cookie is untouched
    because nothing invalidated it: re-issuing would change what the user
    holds without changing what is true, and would quietly extend a
    session from a response whose subject is a credential. The one real
    side effect is named rather than hidden -- the new refresh cookie
    restarts that cookie's seven-day clock.
    """
    body = request.get_json(silent=True) or {}

    user = change_password(
        get_db(),
        get_jwt_identity(),
        # The current password is validated as present and non-empty only;
        # the length policy belongs to the password being SET. See
        # users/validators.py::require_existing_password.
        current_password=require_existing_password(body),
        # The same require_password that flask create-admin and the admin
        # reset use, so the three cannot disagree about the minimum.
        new_password=require_password(body, "new_password"),
    )

    # Deliberately not the profile, not updated_at, not the id: a write
    # endpoint that returns a document invites the client to start
    # trusting it as a read. The version is not in here either -- it is
    # an internal counter, and it travels in a signed cookie only.
    # `user` is the document AFTER the write, so both the identity and
    # the version come from it -- reading the version from anywhere else
    # would race the very change being made.
    identity = str(user["_id"])

    response = jsonify({"message": "Password changed"})
    set_refresh_cookies(
        response,
        create_refresh_token(identity=identity, additional_claims=refresh_claims_for(user)),
    )
    return response, 200


# The one answer POST /forgot-password gives, whatever happened. Defined
# once so no branch can accidentally phrase its own.
RESET_REQUESTED_MESSAGE = "If that email is registered, a reset code has been sent."


@auth_bp.route("/forgot-password", methods=["POST"])
def forgot_password():
    """Send a one-time code to an address, if it belongs to an account.

    Unauthenticated by necessity: somebody who cannot sign in has no
    token to present.

    **The response never varies.** A registered address, an unknown one, a
    deactivated account, and an account that was sent a code a moment ago
    all produce the same 200 and the same body -- so this endpoint cannot
    be asked whether somebody has an account here. `authenticate_user`
    already burns a dummy hash so login cannot be asked that either; an
    endpoint that answered it would hand back what login refuses to.

    That extends to failure. A mail server that refuses the message is
    logged and still answered 200, because "the send failed" is only
    observable for an address that HAS an account, and reporting it would
    be the oracle wearing a different hat.

    The code is stored BEFORE it is sent, which is the reverse of this
    project's send-then-record habit and is explained in
    auth/password_reset.py. In short: the cooldown is the only thing
    stopping this endpoint being pointed at somebody else's inbox, and a
    cooldown checked by a read and committed after an SMTP round trip is
    one that concurrent requests all walk past.

    The one exception is a server with no SMTP configured, and it is
    exactly why load_smtp_settings is called **before** the account is
    looked up: that 503 is decided without reference to the address, so
    every caller gets it alike.

    This is the first mail this project sends from a request thread --
    10 and 11 made mail CLI-only deliberately. smtplib is synchronous, so
    this handler holds its worker for the length of one SMTP connection
    and send (SmtpTransport's own 30-second socket timeout bounds it).
    That is stated rather than engineered around; a queue is a different
    feature.

    Nothing on this path is logged that would say who asked, or for which
    account.
    """
    body = request.get_json(silent=True) or {}

    # A malformed request is a different fact from an address that does
    # not resolve: refusing "email is required" says nothing about who
    # has an account, so it is a 400 rather than a silent 200.
    email = require_email(body)

    smtp_settings = load_smtp_settings(Config)

    pending = issue_reset_code(get_db(), email)

    if pending is not None:
        try:
            # Built inside the try because build_message validates the
            # recipient and raises the same MailerSendError a refused
            # send does -- a stored address that cannot be put in a
            # header must not become a 500.
            with SmtpTransport(smtp_settings) as transport:
                transport.send(
                    build_message(
                        sender=smtp_settings.sender,
                        sender_name=smtp_settings.sender_name,
                        recipient=pending.email,
                        subject=build_reset_subject(),
                        body=build_reset_body(
                            pending.name, pending.code, CODE_TTL_MINUTES
                        ),
                    )
                )
        except MailerSendError:
            # No address, no code, no user id. mailer.py has already
            # logged the transport's own text, which it keeps free of the
            # recipient.
            #
            # The row stays. It holds a code nobody received, which is
            # harmless -- it is unguessable, it expires, and the next
            # request past the cooldown replaces it -- and removing it
            # here would hand back the cooldown slot this request just
            # claimed, which is the one thing that must not become
            # cheap.
            logger.warning("Could not send a password reset email")

    return jsonify({"message": RESET_REQUESTED_MESSAGE}), 200


@auth_bp.route("/reset-password", methods=["POST"])
def reset_password():
    """Spend a code and set a new password. This is not a login.

    No cookie is set, no token is minted, and no profile is returned. The
    code buys exactly one action, once, and the user then signs in
    normally with the password they just chose -- which is what keeps
    this from becoming a second authentication path beside /login.

    `email` is required beside the code, and not for convenience: without
    it the server would have to find *any* account holding a matching
    code, which turns one guess in 10^6 into a birthday problem across
    every outstanding code at once. Scoping each guess to one account is
    what makes the attempts cap mean what it says.

    Every failure of the code is one 400 with one message. See
    auth/errors.py::InvalidResetCodeError for the six outcomes that
    collapse into it and why each alternative is an oracle.

    A successful reset ends every existing session on the account,
    inherited from set_user_password with no code here -- and correctly,
    since somebody resetting a forgotten password may be recovering from
    a compromise rather than forgetfulness.

    Nothing is logged on this path.
    """
    body = request.get_json(silent=True) or {}

    email = require_email(body)
    # Length-capped before it reaches a hash comparison. A code is six
    # digits; without a ceiling a caller could post a megabyte and make
    # the server hash all of it, which is free to send and not free to
    # check. The cap is far above any real code, so it rejects abuse
    # rather than typos.
    code = require_bounded_string(body, "code", MAX_SUBMITTED_CODE_LENGTH)
    # Validated BEFORE the code is spent, so a password that is merely
    # too short costs a message and not the code -- otherwise a length
    # mistake would send the user back to their inbox for a fresh one.
    new_password = require_password(body, "new_password")

    reset_password_with_code(get_db(), email, code, new_password=new_password)

    # Deliberately not the profile and not the id: this endpoint has not
    # authenticated anybody, and a body that looked like a login response
    # would invite a client to treat it as one.
    return jsonify({"message": "Password reset"}), 200


@auth_bp.route("/me", methods=["GET"])
@jwt_required()
def me():
    user = get_user_by_id(get_db(), get_jwt_identity())
    if user is None:
        return jsonify({"error": "Authentication required"}), 401
    return jsonify(to_safe_profile(user)), 200
