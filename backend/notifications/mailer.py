"""Building and sending one email.

**The only module in the project that imports `smtplib` or `email`.**
Everything above it deals in strings and plain dicts, so the sweep can be
tested end to end without a socket, a credential, or a mail server.

The transport is an object the sweep is handed, never one it constructs.
That is what lets tests inject a fake that records `EmailMessage`s, and
it is why `--dry-run` provably opens no connection: the dry-run path is
never given a transport at all.

Plain text only. `set_content` is the single call that fills the body and
there is no `add_alternative` anywhere, so an HTML part is structurally
absent rather than merely unused -- along with the tracking pixel and the
remote image that an HTML part is usually the vehicle for.
"""

import logging
import smtplib
from email.message import EmailMessage
from email.utils import formataddr

from notifications.errors import MailerSendError

logger = logging.getLogger(__name__)

__all__ = [
    "is_safe_header_value",
    "is_sendable_address",
    "build_message",
    "SmtpTransport",
]

# A header value cannot contain a line break. A value carrying one --
# from a malformed or hostile `users.email`, or a mangled SMTP_FROM_NAME
# -- would let the text after the break be read as additional headers,
# which is how a "To" turns into an extra "Bcc". Rejected rather than
# escaped, since no legitimate address or display name contains one.
FORBIDDEN_ADDRESS_CHARACTERS = ("\r", "\n")


def is_safe_header_value(value):
    """Whether a string can be placed in a header without forging one.

    Separate from `is_sendable_address` because the two questions differ:
    a display name is not an address and has no `@` to check, but it ends
    up in the same header and carries the same line-break risk. Shared so
    the rule exists once -- `notifications/settings.py` applies it to the
    operator-configured sender fields, which reach a header by exactly
    the same route the recipient does.
    """
    if not isinstance(value, str):
        return False

    return not any(char in value for char in FORBIDDEN_ADDRESS_CHARACTERS)


def is_sendable_address(value):
    """Whether an address is safe and plausible enough to send to.

    Not an RFC-complete validator, and not trying to be: it answers the
    two questions that matter here -- can this be put in a header without
    forging one, and does it look like an address at all. A wrong-but-
    well-formed address is the mail server's problem to bounce, not
    something this project should try to predict.
    """
    if not isinstance(value, str):
        return False

    address = value.strip()
    if not address or not is_safe_header_value(address):
        return False

    return "@" in address


def build_message(*, sender, sender_name, recipient, subject, body):
    """Assemble one plain-text message.

    The recipient is re-checked here even though the sweep has already
    filtered on `is_sendable_address`. The two checks are not redundant:
    the sweep's is a selection rule that a later refactor could move or
    weaken, while this one guards the moment the value actually becomes a
    header. Header injection is worth failing twice over.
    """
    if not is_sendable_address(recipient):
        raise MailerSendError("Recipient address is not a valid email address")

    message = EmailMessage()
    message["From"] = formataddr((sender_name, sender)) if sender_name else sender
    message["To"] = recipient.strip()
    message["Subject"] = subject
    message.set_content(body)

    return message


class SmtpTransport:
    """One SMTP connection, reused for a whole sweep.

    A context manager, so the connection is closed even when a send
    raises part-way through a run. Opening one connection per student
    would be both slower and a good way to look like a spam source to the
    provider.

    Nothing here logs `settings` -- and `SmtpSettings.password` is
    declared `repr=False`, so even a traceback that captures the object
    cannot print it.
    """

    def __init__(self, settings):
        self._settings = settings
        self._client = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False

    def connect(self):
        settings = self._settings
        try:
            client = smtplib.SMTP(settings.host, settings.port, timeout=30)
            if settings.use_tls:
                client.starttls()
            client.login(settings.username, settings.password)
        except (smtplib.SMTPException, OSError) as exc:
            # The provider's own text is logged for whoever is debugging,
            # but the raised message stays generic: an SMTP server will
            # sometimes echo the username back in an authentication
            # failure, and that string ends up on a terminal.
            logger.exception("Could not connect to the SMTP server")
            raise MailerSendError(
                "Could not connect to the mail server. See server logs for details."
            ) from exc

        self._client = client

    def close(self):
        if self._client is None:
            return

        try:
            self._client.quit()
        except (smtplib.SMTPException, OSError):
            # Already gone, or the server hung up first. Nothing useful is
            # left to do at this point and raising here would mask
            # whatever real error is unwinding the `with` block.
            logger.warning("SMTP connection did not close cleanly", exc_info=True)
        finally:
            self._client = None

    def send(self, message):
        if self._client is None:
            raise MailerSendError("The mail transport is not connected")

        try:
            self._client.send_message(message)
        except (smtplib.SMTPException, OSError) as exc:
            # Deliberately does not name the recipient: this string is
            # surfaced to whoever runs the command, and the sweep already
            # logs which student was skipped.
            logger.exception("SMTP refused a message")
            raise MailerSendError(
                "The mail server refused a message. See server logs for details."
            ) from exc
