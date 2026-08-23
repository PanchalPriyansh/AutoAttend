"""Reading and validating notification configuration off `Config`.

**The only module in the project that reads SMTP configuration.** Keeping
that in one place is what makes "credentials are never logged, never
printed, never stored" a property of a single file rather than a habit
every caller has to remember.

Split into two loaders on purpose:

  - `load_sweep_settings` covers the threshold and the cooldown, which is
    everything a `--dry-run` needs.
  - `load_smtp_settings` covers the credentials, and is called only on a
    path that actually sends.

That split is what lets an admin preview who *would* be mailed on a
machine where mail has not been set up yet -- which is precisely when
they want to look. It also makes the "a dry run opens no connection"
rule structural: the dry-run path never builds these settings at all, so
it cannot open one.

Range checks live here rather than in config.py because they are domain
rules, not type conversions. config.py parses `LOW_ATTENDANCE_THRESHOLD`
into a float the same way it parses `PORT` into an int; deciding that 0
or 140 is not a meaningful attendance bar is this module's job.
"""

from dataclasses import dataclass, field

from notifications.errors import MailerNotConfiguredError
from notifications.mailer import is_safe_header_value, is_sendable_address

__all__ = [
    "SweepSettings",
    "SmtpSettings",
    "load_sweep_settings",
    "load_smtp_settings",
]

# A percentage bar of 0 would mail nobody and a bar above 100 would mail
# everyone; neither is a configuration anyone meant to write, so both are
# rejected loudly instead of running.
MIN_THRESHOLD = 0
MAX_THRESHOLD = 100

MAX_PORT = 65535


@dataclass(frozen=True)
class SweepSettings:
    """The two numbers that decide who is mailed and how often."""

    threshold: float
    cooldown_days: int


@dataclass(frozen=True)
class SmtpSettings:
    """Where mail goes and who it goes as.

    `password` is declared `repr=False`, so the credential cannot reach a
    log line, a traceback, or a `print()` through the automatic repr that
    every other field gets. That is the one field on this object nothing
    should ever be able to display by accident.
    """

    host: str
    port: int
    username: str
    password: str = field(repr=False)
    sender: str
    sender_name: str
    use_tls: bool


def _is_missing(value):
    return value is None or not str(value).strip()


def _require_all_present(pairs):
    """Report every missing setting at once, not just the first.

    Setting up SMTP means filling in four variables. Failing on one at a
    time turns that into four runs, each ending in a different error --
    so the check collects them and names the whole set.

    The message names *settings*, never their values. For SMTP_PASSWORD
    that distinction is the entire point, and applying the same rule to
    all four means there is no special case anyone can forget.
    """
    missing = [name for name, value in pairs if _is_missing(value)]
    if not missing:
        return

    if len(missing) == 1:
        raise MailerNotConfiguredError(f"{missing[0]} is not set")

    raise MailerNotConfiguredError(
        f"These settings are not set: {', '.join(missing)}"
    )


def load_sweep_settings(config):
    """The threshold and cooldown, validated. Needed by every run,
    including a dry one.
    """
    threshold = getattr(config, "LOW_ATTENDANCE_THRESHOLD", None)
    cooldown_days = getattr(config, "NOTIFICATION_COOLDOWN_DAYS", None)

    if threshold is None:
        raise MailerNotConfiguredError("LOW_ATTENDANCE_THRESHOLD is not set")
    if cooldown_days is None:
        raise MailerNotConfiguredError("NOTIFICATION_COOLDOWN_DAYS is not set")

    # A bool is an int in Python, and `True` would silently become a 1%
    # bar. Rejected explicitly rather than trusted to the range check.
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise MailerNotConfiguredError("LOW_ATTENDANCE_THRESHOLD must be a number")
    if isinstance(cooldown_days, bool) or not isinstance(cooldown_days, int):
        raise MailerNotConfiguredError("NOTIFICATION_COOLDOWN_DAYS must be a whole number")

    if not MIN_THRESHOLD < threshold <= MAX_THRESHOLD:
        raise MailerNotConfiguredError(
            "LOW_ATTENDANCE_THRESHOLD must be greater than "
            f"{MIN_THRESHOLD} and at most {MAX_THRESHOLD}"
        )
    # 0 is allowed and means "no cooldown" -- every run mails everyone who
    # is still below the bar. Negative is not a shorter cooldown, it is a
    # typo, and it would silently widen the window into the future.
    if cooldown_days < 0:
        raise MailerNotConfiguredError("NOTIFICATION_COOLDOWN_DAYS must not be negative")

    return SweepSettings(threshold=float(threshold), cooldown_days=int(cooldown_days))


def load_smtp_settings(config):
    """The credentials and sender identity. Only ever called on a path
    that actually sends mail.
    """
    host = getattr(config, "SMTP_HOST", None)
    username = getattr(config, "SMTP_USERNAME", None)
    password = getattr(config, "SMTP_PASSWORD", None)
    sender = getattr(config, "SMTP_FROM_ADDRESS", None)

    _require_all_present(
        [
            ("SMTP_HOST", host),
            ("SMTP_USERNAME", username),
            ("SMTP_PASSWORD", password),
            ("SMTP_FROM_ADDRESS", sender),
        ]
    )

    port = getattr(config, "SMTP_PORT", None)
    if isinstance(port, bool) or not isinstance(port, int):
        raise MailerNotConfiguredError("SMTP_PORT must be a whole number")
    if not 0 < port <= MAX_PORT:
        raise MailerNotConfiguredError(f"SMTP_PORT must be between 1 and {MAX_PORT}")

    sender = str(sender).strip()
    # Held to the same standard as a recipient. These two values reach an
    # email header by exactly the same route `users.email` does -- the
    # only difference is that an operator sets them rather than an admin,
    # which makes a line break here a misconfiguration rather than an
    # attack. Checking both keeps the guard symmetric, so nobody later
    # has to work out why one side of the header was validated and the
    # other was trusted.
    #
    # Not a full address parser -- just enough to catch a hostname or a
    # display name pasted into the wrong variable, which would otherwise
    # fail deep inside the transport with a message nobody can act on.
    if not is_sendable_address(sender):
        raise MailerNotConfiguredError("SMTP_FROM_ADDRESS must be an email address")

    sender_name = str(getattr(config, "SMTP_FROM_NAME", "") or "").strip()
    if not is_safe_header_value(sender_name):
        raise MailerNotConfiguredError(
            "SMTP_FROM_NAME must not contain a line break"
        )

    return SmtpSettings(
        host=str(host).strip(),
        port=port,
        username=str(username).strip(),
        # Deliberately not stripped, unlike every other value here: a
        # password may legitimately begin or end with a space, and quietly
        # trimming it would produce an authentication failure that looks
        # like a wrong password rather than a mangled one.
        password=str(password),
        sender=sender,
        sender_name=sender_name,
        use_tls=bool(getattr(config, "SMTP_USE_TLS", True)),
    )
