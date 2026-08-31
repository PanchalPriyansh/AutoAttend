"""Domain exceptions specific to notifications.

Bad input, missing target, and conflicting state already exist in
common/errors.py and are reused rather than redeclared, following
attendance/errors.py's precedent: only what is genuinely new to this
feature is declared here.

Both of these began life reachable only from a terminal, through the
`notify-low-attendance` CLI command, which turns them into a one-line
message and a non-zero exit. That is still the only way
`MailerSendError` surfaces: 25-forgot-password catches it on the request
path and answers 200 regardless, so it never becomes a status code.

`MailerNotConfiguredError` is no longer terminal-only. 25-forgot-password
put transactional mail on a request thread, and routes/auth.py maps this
one to **503** -- deliberately before any user lookup, so a deployment
with no SMTP configured answers the same thing to every caller instead of
only to addresses that exist. That handler does NOT return the message
below; it logs it and sends a generic one, because these messages name a
setting and an anonymous caller has no business learning which.

The messages below are read by whoever runs that command. They may name a
*setting* that is missing or wrong; they must never carry its value, so
that no credential can reach a terminal, a log file, or a shell history.
"""

from common.errors import AppError

__all__ = ["MailerNotConfiguredError", "MailerSendError"]


class MailerNotConfiguredError(AppError):
    """SMTP or sweep configuration is missing, unparseable, or out of range.

    Raised by settings.py before anything is read from the database or
    sent, so a misconfigured deployment fails immediately and completely
    rather than half-way through a sweep with some students mailed. On
    the request path 25-forgot-password added, the same "before anything
    is read" property is what keeps a 503 from depending on whether the
    address exists -- see routes/auth.py, which maps this to 503 with a
    generic body.
    """


class MailerSendError(AppError):
    """The transport refused a message, or the recipient address is not
    one that can safely be sent to.

    Caught per student by the sweep, which logs it and moves on: one bad
    address must not stop every other student being warned. Because no
    `attendance_notifications` row is written for a failed send, the next
    run retries that student.
    """
