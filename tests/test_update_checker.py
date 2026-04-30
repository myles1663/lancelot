import logging
import socket
import urllib.error
import urllib.request

from src.core.update_checker import UpdateChecker


def test_dns_failure_is_logged_as_info(monkeypatch, caplog):
    def fake_urlopen(*args, **kwargs):
        raise urllib.error.URLError(socket.gaierror(-2, 'Name or service not known'))

    monkeypatch.setattr(urllib.request, 'urlopen', fake_urlopen)
    checker = UpdateChecker()

    with caplog.at_level(logging.INFO, logger='lancelot.update_checker'):
        status = checker.force_check()

    assert 'Name or service not known' in (status['check_error'] or '')
    assert status['check_state'] == 'offline'
    assert status['check_error_kind'] == 'network_unreachable'
    assert status['next_check_after'] is not None
    assert 'Version check deferred' not in caplog.text
    assert 'Version check failed' not in caplog.text


def test_dns_failure_debug_log_is_available_for_diagnostics(monkeypatch, caplog):
    def fake_urlopen(*args, **kwargs):
        raise urllib.error.URLError(socket.gaierror(-2, 'Name or service not known'))

    monkeypatch.setattr(urllib.request, 'urlopen', fake_urlopen)
    checker = UpdateChecker()

    with caplog.at_level(logging.DEBUG, logger='lancelot.update_checker'):
        checker.force_check()

    assert 'Version check deferred' in caplog.text
    assert 'will retry' in caplog.text.lower()


def test_http_error_still_logs_warning(monkeypatch, caplog):
    def fake_urlopen(*args, **kwargs):
        raise urllib.error.HTTPError(
            url='https://api.projectlancelot.dev/v1/version',
            code=503,
            msg='Service Unavailable',
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr(urllib.request, 'urlopen', fake_urlopen)
    checker = UpdateChecker()

    with caplog.at_level(logging.INFO, logger='lancelot.update_checker'):
        status = checker.force_check()

    assert status['check_state'] == 'failed'
    assert status['check_error_kind'] == 'manifest_http_error'
    assert 'Version check failed' in caplog.text
