"""Domain exceptions specific to notifications.

Bad input, missing target, and conflicting state already exist in
common/errors.py and are reused rather than redeclared, following
attendance/errors.py's precedent: only what is genuinely new to this
feature is declared here.

Unlike every other error module in the project, these carry no HTTP
status mapping -- nothing in this package is reachable from a route. They
surface at a terminal, through the `notify-low-attendance` CLI command,
which turns them into a one-line message and a non-zero exit.

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
    rather than half-way through a sweep with some students mailed.
    """


class MailerSendError(AppError):
    """The transport refused a message, or the recipient address is not
    one that can safely be sent to.

    Caught per student by the sweep, which logs it and moves on: one bad
    address must not stop every other student being warned. Because no
    `attendance_notifications` row is written for a failed send, the next
    run retries that student.
    """
