"""Tests for backend/notifications/mailer.py.

Spec contract under test (.claude/specs/10-low-attendance-notifications.md,
"Backend" + "Rules for implementation" + "Definition of done"):
  - `is_sendable_address(value)` rejects anything that is not a string,
    is blank, lacks "@", or carries a CR/LF (header-injection defence --
    rule "header injection").
  - `build_message(...)` builds a plain-text `EmailMessage`
    (`set_content`, no `add_alternative`, so no HTML part and no
    attachment can exist), re-validates the recipient the same way, and
    raises `MailerSendError` for an address that fails that check.
  - `SmtpTransport` is a context manager around one `smtplib.SMTP`
    connection: `connect()` opens it, optionally calls `starttls()`, and
    logs in; `send()` delegates to `send_message`; `close()`/`__exit__`
    always calls `quit()` and swallows a failure to do so cleanly;
    connection and send failures are translated to `MailerSendError`
    without leaking the password into the raised message.

Every test here monkeypatches `notifications.mailer.smtplib.SMTP` with an
in-process fake -- no test in this file ever opens a socket or contacts a
real mail server, and no real credential is used anywhere.
"""

import smtplib

import pytest

from notifications.errors import MailerSendError
from notifications.mailer import (
    SmtpTransport,
    build_message,
    is_safe_header_value,
    is_sendable_address,
)
from notifications.settings import SmtpSettings


def _settings(**overrides):
    defaults = dict(
        host="smtp.example.test",
        port=587,
        username="bot@example.test",
        password="test-only-fake-password",
        sender="noreply@example.test",
        sender_name="AutoAttend",
        use_tls=True,
    )
    defaults.update(overrides)
    return SmtpSettings(**defaults)


# --- is_sendable_address ---------------------------------------------------


class TestIsSendableAddress:
    @pytest.mark.parametrize(
        "value",
        [
            "student@example.test",
            "  student@example.test  ",
            "first.last+tag@example.test",
        ],
    )
    def test_plausible_addresses_are_sendable(self, value):
        assert is_sendable_address(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            None,
            123,
            "",
            "   ",
            "not-an-email-address",
            # A CR/LF embedded in the middle of the address -- the shape a
            # real header-injection attempt takes, since a line break at
            # the very edge is removed by the same strip() that trims
            # ordinary whitespace and therefore carries nothing after it
            # for a receiving header parser to read as an extra header.
            "student@example.test\r\nBcc: attacker@example.test",
            "student\r@example.test",
            "student\n@example.test",
        ],
    )
    def test_implausible_or_hostile_addresses_are_rejected(self, value):
        assert is_sendable_address(value) is False

    @pytest.mark.parametrize("value", ["student@example.test\r", "student@example.test\n"])
    def test_a_trailing_line_break_with_nothing_after_it_is_stripped_and_accepted(self, value):
        """Documents the actual, deliberate behaviour: `is_sendable_address`
        strips the value before checking for a forbidden character, so a
        line break at the very edge -- which carries no injected header
        text after it -- is removed the same way ordinary trailing
        whitespace is, rather than being treated as hostile.
        """
        assert is_sendable_address(value) is True


# --- build_message -----------------------------------------------------------


class TestBuildMessage:
    def test_builds_a_message_with_the_expected_recipient_subject_and_body(self):
        message = build_message(
            sender="noreply@example.test",
            sender_name="",
            recipient="student@example.test",
            subject="AutoAttend: your attendance is low in 1 class",
            body="Hello Alice Test,\n\nsome body text\n",
        )

        assert message["To"] == "student@example.test"
        assert message["Subject"] == "AutoAttend: your attendance is low in 1 class"
        assert "some body text" in message.get_content()

    def test_message_is_plain_text_with_no_html_part_and_no_attachment(self):
        message = build_message(
            sender="noreply@example.test",
            sender_name="",
            recipient="student@example.test",
            subject="subject",
            body="body",
        )

        assert message.get_content_type() == "text/plain"
        assert message.is_multipart() is False
        assert list(message.iter_attachments()) == []

    def test_sender_name_is_folded_into_the_from_header_when_given(self):
        message = build_message(
            sender="noreply@example.test",
            sender_name="AutoAttend",
            recipient="student@example.test",
            subject="subject",
            body="body",
        )

        assert "AutoAttend" in message["From"]
        assert "noreply@example.test" in message["From"]

    def test_from_header_is_the_plain_address_when_no_sender_name_is_given(self):
        message = build_message(
            sender="noreply@example.test",
            sender_name="",
            recipient="student@example.test",
            subject="subject",
            body="body",
        )

        assert message["From"] == "noreply@example.test"

    def test_recipient_is_stripped_of_surrounding_whitespace(self):
        message = build_message(
            sender="noreply@example.test",
            sender_name="",
            recipient="  student@example.test  ",
            subject="subject",
            body="body",
        )

        assert message["To"] == "student@example.test"

    @pytest.mark.parametrize(
        "recipient",
        [
            "student@example.test\r\nBcc: attacker@example.test",
            "student\r@example.test",
            "student\n@example.test",
        ],
    )
    def test_a_recipient_carrying_an_embedded_line_break_is_rejected(self, recipient):
        with pytest.raises(MailerSendError):
            build_message(
                sender="noreply@example.test",
                sender_name="",
                recipient=recipient,
                subject="subject",
                body="body",
            )

    def test_a_recipient_without_an_at_symbol_is_rejected(self):
        with pytest.raises(MailerSendError):
            build_message(
                sender="noreply@example.test",
                sender_name="",
                recipient="not-an-email-address",
                subject="subject",
                body="body",
            )


# --- SmtpTransport -----------------------------------------------------------


class _FakeSmtpClient:
    """Stands in for `smtplib.SMTP`. Every test in this class replaces
    `notifications.mailer.smtplib.SMTP` with a factory producing one of
    these, so no test ever opens a real socket.
    """

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.login_calls = []
        self.sent_messages = []
        self.quit_called = False

    def starttls(self):
        self.started_tls = True

    def login(self, username, password):
        self.login_calls.append((username, password))

    def send_message(self, message):
        self.sent_messages.append(message)

    def quit(self):
        self.quit_called = True


@pytest.fixture
def fake_smtp(monkeypatch):
    """Patches `smtplib.SMTP` with a factory that always returns the same
    `_FakeSmtpClient` instance, so the test can inspect it afterwards.
    """
    created = {}

    def factory(host, port, timeout=None):
        client = _FakeSmtpClient(host, port, timeout=timeout)
        created["client"] = client
        return client

    monkeypatch.setattr("notifications.mailer.smtplib.SMTP", factory)
    return created


class TestSmtpTransportConnect:
    def test_connect_opens_the_configured_host_and_port_and_logs_in(self, fake_smtp):
        settings = _settings(host="mail.example.test", port=2525, use_tls=True)
        transport = SmtpTransport(settings)

        transport.connect()

        client = fake_smtp["client"]
        assert client.host == "mail.example.test"
        assert client.port == 2525
        assert client.started_tls is True
        assert client.login_calls == [(settings.username, settings.password)]

    def test_connect_skips_starttls_when_use_tls_is_false(self, fake_smtp):
        settings = _settings(use_tls=False)
        transport = SmtpTransport(settings)

        transport.connect()

        assert fake_smtp["client"].started_tls is False

    def test_a_connection_failure_is_translated_to_mailer_send_error(self, monkeypatch):
        def factory(host, port, timeout=None):
            raise smtplib.SMTPException("connection refused")

        monkeypatch.setattr("notifications.mailer.smtplib.SMTP", factory)
        settings = _settings(password="do-not-leak-this-password")
        transport = SmtpTransport(settings)

        with pytest.raises(MailerSendError) as excinfo:
            transport.connect()

        assert "do-not-leak-this-password" not in str(excinfo.value)

    def test_a_login_failure_is_translated_to_mailer_send_error(self, monkeypatch):
        class _RejectingClient(_FakeSmtpClient):
            def login(self, username, password):
                raise smtplib.SMTPAuthenticationError(535, b"authentication failed")

        monkeypatch.setattr(
            "notifications.mailer.smtplib.SMTP",
            lambda host, port, timeout=None: _RejectingClient(host, port, timeout),
        )
        transport = SmtpTransport(_settings(password="do-not-leak-this-password"))

        with pytest.raises(MailerSendError) as excinfo:
            transport.connect()

        assert "do-not-leak-this-password" not in str(excinfo.value)


class TestSmtpTransportSend:
    def test_send_delegates_to_the_underlying_client(self, fake_smtp):
        transport = SmtpTransport(_settings())
        transport.connect()
        message = build_message(
            sender="noreply@example.test", sender_name="", recipient="student@example.test",
            subject="subject", body="body",
        )

        transport.send(message)

        assert fake_smtp["client"].sent_messages == [message]

    def test_sending_without_connecting_first_raises_mailer_send_error(self):
        transport = SmtpTransport(_settings())
        message = build_message(
            sender="noreply@example.test", sender_name="", recipient="student@example.test",
            subject="subject", body="body",
        )

        with pytest.raises(MailerSendError):
            transport.send(message)

    def test_a_send_failure_is_translated_to_mailer_send_error(self, monkeypatch):
        class _RefusingClient(_FakeSmtpClient):
            def send_message(self, message):
                raise smtplib.SMTPException("mailbox unavailable")

        monkeypatch.setattr(
            "notifications.mailer.smtplib.SMTP",
            lambda host, port, timeout=None: _RefusingClient(host, port, timeout),
        )
        transport = SmtpTransport(_settings())
        transport.connect()
        message = build_message(
            sender="noreply@example.test", sender_name="", recipient="student@example.test",
            subject="subject", body="body",
        )

        with pytest.raises(MailerSendError):
            transport.send(message)


class TestSmtpTransportContextManagerAndClose:
    def test_context_manager_connects_on_enter_and_closes_on_exit(self, fake_smtp):
        with SmtpTransport(_settings()) as transport:
            assert transport is not None
            assert fake_smtp["client"].quit_called is False

        assert fake_smtp["client"].quit_called is True

    def test_close_is_still_called_when_an_exception_propagates_through_the_with_block(self, fake_smtp):
        with pytest.raises(RuntimeError):
            with SmtpTransport(_settings()):
                raise RuntimeError("something else went wrong inside the block")

        assert fake_smtp["client"].quit_called is True

    def test_close_swallows_a_failure_to_quit_cleanly(self, monkeypatch):
        class _StubbornClient(_FakeSmtpClient):
            def quit(self):
                raise smtplib.SMTPException("server already hung up")

        monkeypatch.setattr(
            "notifications.mailer.smtplib.SMTP",
            lambda host, port, timeout=None: _StubbornClient(host, port, timeout),
        )
        transport = SmtpTransport(_settings())
        transport.connect()

        transport.close()  # must not raise

    def test_close_is_a_no_op_when_never_connected(self):
        transport = SmtpTransport(_settings())

        transport.close()  # must not raise


class TestIsSafeHeaderValue:
    """Added after the security review of spec 10.

    Extracted from `is_sendable_address` so the line-break rule exists
    once and can also be applied to a display name, which has no "@" to
    check but lands in the same header. `notifications/settings.py`
    applies it to the operator-configured sender fields, which reach a
    header by exactly the route `users.email` does.
    """

    @pytest.mark.parametrize(
        "value",
        ["AutoAttend", "AutoAttend Notifications", "", "  spaced  ", "a@b.test"],
    )
    def test_a_value_without_a_line_break_is_safe(self, value):
        assert is_safe_header_value(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            "AutoAttend\r\nBcc: elsewhere@example.test",
            "AutoAttend\nBcc: elsewhere@example.test",
            "AutoAttend\rBcc: elsewhere@example.test",
            "\n",
        ],
    )
    def test_a_value_carrying_a_line_break_is_not_safe(self, value):
        assert is_safe_header_value(value) is False

    @pytest.mark.parametrize("value", [None, 42, b"AutoAttend", ["AutoAttend"]])
    def test_a_non_string_is_not_safe(self, value):
        assert is_safe_header_value(value) is False
