"""The wording of a password-reset email.

Pure string construction, the same discipline `notifications/messages.py`
keeps: no database, no SMTP, no Flask, and no Mongo document reaches this
module -- callers hand it a name, a code, and a number of minutes. A
function that never sees a document cannot leak a field off one, and it
makes the wording assertable without a transport, a socket, or a
credential.

Three things this text must never become:

  - **A link.** There is no link-based reset in this project, so there is
    nothing here to click, and that is worth keeping even if one is added
    later: a password email that trains people to follow links is a
    phishing lesson delivered by the system that will later be
    impersonated. No URL appears below, and no HTML part exists to hide
    one in -- `notifications/mailer.py` calls `set_content` and nothing
    else.
  - **An accusation.** A reset request is not evidence that anything is
    wrong. The unrequested case is addressed plainly and without alarm,
    because the honest answer -- nothing has changed yet, ignore this --
    is also the reassuring one.
  - **A carrier for anything but the code.** No account details, no role,
    no attendance figures, and nothing about anybody else.

The code is in the body and never in the subject: a subject line shows up
in a lock-screen preview and over the shoulder of whoever is next to the
recipient, which is precisely the reader this code is not for.
"""

__all__ = ["build_reset_subject", "build_reset_body"]

# Matches notifications/messages.py's prefix, so both automated messages
# from this system are recognisable as coming from it.
SUBJECT_PREFIX = "AutoAttend"


def build_reset_subject():
    """One subject line, carrying no code and no figures.

    Takes no arguments, and is a function rather than a constant so that
    it is called the same way build_reset_body is -- and so a later
    change that needs an argument does not have to move every caller.
    """
    return f"{SUBJECT_PREFIX}: your password reset code"


def build_reset_body(name, code, ttl_minutes):
    """The full plain-text body for one reset request.

    `code` is rendered on its own indented line rather than inline in a
    sentence: it is the one thing the reader is here to copy, and a
    six-digit string in the middle of a paragraph is easy to lose and
    easy to mistranscribe.

    The single-use rule and the expiry are both stated, because both
    change what the reader should do -- a code that silently stopped
    working would otherwise look like the system failing rather than the
    system working.
    """
    greeting = f"Hello {name}," if name else "Hello,"
    minutes = "minute" if ttl_minutes == 1 else "minutes"

    lines = [
        greeting,
        "",
        "Someone asked to reset the password on your AutoAttend account.",
        "",
        "Your reset code is:",
        "",
        f"    {code}",
        "",
        f"It can be used once, and it expires in {ttl_minutes} {minutes}.",
        "Enter it on the AutoAttend password reset screen, along with the new",
        "password you want to use.",
        "",
        "If you did not ask for this, you can ignore this message. Nothing has",
        "changed yet, and your current password still works.",
        "",
        "This message was sent automatically. Please do not reply to it.",
    ]

    return "\n".join(lines) + "\n"
