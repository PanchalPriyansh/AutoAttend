"""Tests for backend/notifications/settings.py.

Spec contract under test (.claude/specs/10-low-attendance-notifications.md,
"Backend" + "Rules for implementation" + "Definition of done"):
  - `load_sweep_settings(config)` reads `LOW_ATTENDANCE_THRESHOLD` and
    `NOTIFICATION_COOLDOWN_DAYS`; both are required to be present, numeric
    (not a bool masquerading as one), and in range: threshold strictly
    greater than 0 and at most 100, cooldown a non-negative whole number
    (0 is a valid "no cooldown" value).
  - `load_smtp_settings(config)` reads `SMTP_HOST`, `SMTP_PORT`,
    `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_ADDRESS`,
    `SMTP_FROM_NAME`, and `SMTP_USE_TLS`. The four credential-shaped
    values (`SMTP_HOST`, `SMTP_USERNAME`, `SMTP_PASSWORD`,
    `SMTP_FROM_ADDRESS`) are all required and every missing one is named
    in a single error, never just the first. `SMTP_PORT` must be a whole
    number in `(0, 65535]`. `SMTP_FROM_ADDRESS` must contain "@".
    `SMTP_PASSWORD` is never stripped (unlike every other value) and is
    excluded from the settings object's repr.
  - Both loaders raise `MailerNotConfiguredError`, never leaking a
    credential's *value* into the message -- only ever a setting's name.

`Config` is never touched here: each test builds its own minimal
namespace object and passes it directly to the loader, exactly the
`config` parameter both functions accept. No real credential, host, or
address is used anywhere -- every value is an obviously-fake
`example.test`-style placeholder.
"""

from types import SimpleNamespace

import pytest

from notifications.errors import MailerNotConfiguredError
from notifications.settings import load_smtp_settings, load_sweep_settings


def _config(**overrides):
    return SimpleNamespace(**overrides)


# --- load_sweep_settings -------------------------------------------------


class TestLoadSweepSettingsHappyPath:
    def test_valid_threshold_and_cooldown_are_read_and_typed(self):
        settings = load_sweep_settings(
            _config(LOW_ATTENDANCE_THRESHOLD=75, NOTIFICATION_COOLDOWN_DAYS=7)
        )

        assert settings.threshold == 75.0
        assert isinstance(settings.threshold, float)
        assert settings.cooldown_days == 7
        assert isinstance(settings.cooldown_days, int)

    def test_cooldown_of_zero_is_allowed_and_means_no_cooldown(self):
        settings = load_sweep_settings(
            _config(LOW_ATTENDANCE_THRESHOLD=75, NOTIFICATION_COOLDOWN_DAYS=0)
        )

        assert settings.cooldown_days == 0

    @pytest.mark.parametrize("threshold", [0.01, 1, 50, 99.9, 100])
    def test_thresholds_within_the_open_closed_range_are_accepted(self, threshold):
        settings = load_sweep_settings(
            _config(LOW_ATTENDANCE_THRESHOLD=threshold, NOTIFICATION_COOLDOWN_DAYS=7)
        )

        assert settings.threshold == float(threshold)


class TestLoadSweepSettingsMissingValues:
    def test_missing_threshold_raises(self):
        with pytest.raises(MailerNotConfiguredError, match="LOW_ATTENDANCE_THRESHOLD"):
            load_sweep_settings(_config(NOTIFICATION_COOLDOWN_DAYS=7))

    def test_missing_cooldown_raises(self):
        with pytest.raises(MailerNotConfiguredError, match="NOTIFICATION_COOLDOWN_DAYS"):
            load_sweep_settings(_config(LOW_ATTENDANCE_THRESHOLD=75))


class TestLoadSweepSettingsTypeValidation:
    def test_non_numeric_threshold_raises(self):
        with pytest.raises(MailerNotConfiguredError):
            load_sweep_settings(
                _config(LOW_ATTENDANCE_THRESHOLD="not-a-number", NOTIFICATION_COOLDOWN_DAYS=7)
            )

    def test_a_bool_threshold_is_rejected_even_though_bool_is_a_subclass_of_int(self):
        with pytest.raises(MailerNotConfiguredError):
            load_sweep_settings(_config(LOW_ATTENDANCE_THRESHOLD=True, NOTIFICATION_COOLDOWN_DAYS=7))

    def test_a_non_whole_number_cooldown_raises(self):
        with pytest.raises(MailerNotConfiguredError):
            load_sweep_settings(_config(LOW_ATTENDANCE_THRESHOLD=75, NOTIFICATION_COOLDOWN_DAYS=7.5))

    def test_a_bool_cooldown_is_rejected(self):
        with pytest.raises(MailerNotConfiguredError):
            load_sweep_settings(_config(LOW_ATTENDANCE_THRESHOLD=75, NOTIFICATION_COOLDOWN_DAYS=True))


class TestLoadSweepSettingsRangeValidation:
    @pytest.mark.parametrize("threshold", [0, -1, 100.01, 140])
    def test_thresholds_outside_the_open_closed_range_are_rejected(self, threshold):
        with pytest.raises(MailerNotConfiguredError):
            load_sweep_settings(
                _config(LOW_ATTENDANCE_THRESHOLD=threshold, NOTIFICATION_COOLDOWN_DAYS=7)
            )

    def test_a_negative_cooldown_raises(self):
        with pytest.raises(MailerNotConfiguredError):
            load_sweep_settings(_config(LOW_ATTENDANCE_THRESHOLD=75, NOTIFICATION_COOLDOWN_DAYS=-1))


# --- load_smtp_settings ---------------------------------------------------


def _smtp_config(**overrides):
    defaults = dict(
        SMTP_HOST="smtp.example.test",
        SMTP_PORT=587,
        SMTP_USERNAME="bot@example.test",
        SMTP_PASSWORD="test-only-fake-password",
        SMTP_FROM_ADDRESS="noreply@example.test",
        SMTP_FROM_NAME="AutoAttend",
        SMTP_USE_TLS=True,
    )
    defaults.update(overrides)
    return _config(**defaults)


class TestLoadSmtpSettingsHappyPath:
    def test_all_required_values_are_read_onto_the_settings_object(self):
        settings = load_smtp_settings(_smtp_config())

        assert settings.host == "smtp.example.test"
        assert settings.port == 587
        assert settings.username == "bot@example.test"
        assert settings.password == "test-only-fake-password"
        assert settings.sender == "noreply@example.test"
        assert settings.sender_name == "AutoAttend"
        assert settings.use_tls is True

    def test_host_username_and_sender_are_stripped_of_surrounding_whitespace(self):
        settings = load_smtp_settings(
            _smtp_config(
                SMTP_HOST="  smtp.example.test  ",
                SMTP_USERNAME="  bot@example.test  ",
                SMTP_FROM_ADDRESS="  noreply@example.test  ",
            )
        )

        assert settings.host == "smtp.example.test"
        assert settings.username == "bot@example.test"
        assert settings.sender == "noreply@example.test"

    def test_password_is_never_stripped_unlike_the_other_fields(self):
        settings = load_smtp_settings(_smtp_config(SMTP_PASSWORD="  padded-password  "))

        assert settings.password == "  padded-password  "

    def test_from_name_defaults_to_empty_string_when_unset(self):
        config = _smtp_config()
        del config.SMTP_FROM_NAME

        settings = load_smtp_settings(config)

        assert settings.sender_name == ""

    def test_use_tls_defaults_to_true_when_unset(self):
        config = _smtp_config()
        del config.SMTP_USE_TLS

        settings = load_smtp_settings(config)

        assert settings.use_tls is True

    def test_use_tls_can_be_explicitly_disabled(self):
        settings = load_smtp_settings(_smtp_config(SMTP_USE_TLS=False))

        assert settings.use_tls is False


class TestLoadSmtpSettingsMissingCredentials:
    def test_a_single_missing_setting_is_named_alone(self):
        config = _smtp_config()
        del config.SMTP_HOST

        with pytest.raises(MailerNotConfiguredError) as excinfo:
            load_smtp_settings(config)

        message = str(excinfo.value)
        assert "SMTP_HOST" in message
        assert "SMTP_USERNAME" not in message
        assert "SMTP_PASSWORD" not in message
        assert "SMTP_FROM_ADDRESS" not in message

    @pytest.mark.parametrize(
        "field", ["SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM_ADDRESS"]
    )
    def test_a_blank_or_whitespace_only_value_is_also_treated_as_missing(self, field):
        with pytest.raises(MailerNotConfiguredError, match=field):
            load_smtp_settings(_smtp_config(**{field: "   "}))

    def test_every_missing_setting_is_named_in_one_error_not_just_the_first(self):
        config = _smtp_config()
        del config.SMTP_HOST
        del config.SMTP_USERNAME

        with pytest.raises(MailerNotConfiguredError) as excinfo:
            load_smtp_settings(config)

        message = str(excinfo.value)
        assert "SMTP_HOST" in message
        assert "SMTP_USERNAME" in message

    def test_the_real_password_value_never_appears_in_the_error_message(self):
        config = _smtp_config(SMTP_PASSWORD="super-secret-value-should-not-leak")
        del config.SMTP_HOST

        with pytest.raises(MailerNotConfiguredError) as excinfo:
            load_smtp_settings(config)

        assert "super-secret-value-should-not-leak" not in str(excinfo.value)


class TestLoadSmtpSettingsPortValidation:
    @pytest.mark.parametrize("port", [0, -1, 65536, 100000])
    def test_a_port_outside_one_to_max_is_rejected(self, port):
        with pytest.raises(MailerNotConfiguredError):
            load_smtp_settings(_smtp_config(SMTP_PORT=port))

    def test_a_non_integer_port_is_rejected(self):
        with pytest.raises(MailerNotConfiguredError):
            load_smtp_settings(_smtp_config(SMTP_PORT="587"))

    def test_a_bool_port_is_rejected(self):
        with pytest.raises(MailerNotConfiguredError):
            load_smtp_settings(_smtp_config(SMTP_PORT=True))

    def test_the_maximum_valid_port_is_accepted(self):
        settings = load_smtp_settings(_smtp_config(SMTP_PORT=65535))

        assert settings.port == 65535


class TestLoadSmtpSettingsFromAddressValidation:
    def test_a_from_address_without_an_at_symbol_is_rejected(self):
        with pytest.raises(MailerNotConfiguredError):
            load_smtp_settings(_smtp_config(SMTP_FROM_ADDRESS="not-an-email-address"))


class TestSenderFieldsAreHeldToTheSameHeaderRuleAsARecipient:
    """Added after the security review of spec 10.

    `SMTP_FROM_ADDRESS` and `SMTP_FROM_NAME` reach an email header by
    exactly the same route `users.email` does, so a line break in either
    carries the same header-forging risk. The difference is only who sets
    them -- an operator rather than an admin -- which makes this a
    misconfiguration rather than an attack, and a reason to fail loudly
    at load time rather than to skip the check.
    """

    @pytest.mark.parametrize("break_character", ["\r", "\n", "\r\n"])
    def test_a_from_address_containing_a_line_break_is_rejected(self, break_character):
        poisoned = f"noreply@example.test{break_character}Bcc: elsewhere@example.test"

        with pytest.raises(MailerNotConfiguredError):
            load_smtp_settings(_smtp_config(SMTP_FROM_ADDRESS=poisoned))

    @pytest.mark.parametrize("break_character", ["\r", "\n", "\r\n"])
    def test_a_from_name_containing_a_line_break_is_rejected(self, break_character):
        poisoned = f"AutoAttend{break_character}Bcc: elsewhere@example.test"

        with pytest.raises(MailerNotConfiguredError):
            load_smtp_settings(_smtp_config(SMTP_FROM_NAME=poisoned))

    def test_the_error_for_a_poisoned_from_name_names_the_setting(self):
        with pytest.raises(MailerNotConfiguredError) as exc_info:
            load_smtp_settings(_smtp_config(SMTP_FROM_NAME="AutoAttend\nBcc: x@y.test"))

        assert "SMTP_FROM_NAME" in str(exc_info.value)

    def test_an_ordinary_sender_name_and_address_are_still_accepted(self):
        settings = load_smtp_settings(
            _smtp_config(SMTP_FROM_NAME="AutoAttend Notifications")
        )

        assert settings.sender_name == "AutoAttend Notifications"
        assert settings.sender == "noreply@example.test"


class TestSmtpSettingsPasswordNeverLeaksThroughRepr:
    def test_the_password_field_does_not_appear_in_the_settings_objects_repr(self):
        settings = load_smtp_settings(_smtp_config(SMTP_PASSWORD="do-not-print-this-value"))

        assert "do-not-print-this-value" not in repr(settings)
