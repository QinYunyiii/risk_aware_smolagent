"""Offline parser regression: actual retained trace and explicitly synthetic cases.

Neither synthetic version label is a captured full transcript of that OpenSSL build.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import socket
import subprocess

import pytest

from risk_agent.collection import remote_local_tls_isolation as isolation


FIXTURE = Path(__file__).parent / 'fixtures/tls/remote_loopback_openssl_3_0_2_retained.json'
CA = b'public classifier fixture CA'
BRIEF = [
    ('stderr', 'CONNECTION ESTABLISHED'),
    ('stderr', 'Protocol version: TLSv1.3'),
    ('stderr', 'Ciphersuite: TLS_AES_256_GCM_SHA384'),
    ('stderr', 'Verification: OK'),
]
LEGACY = [
    ('stderr', 'SSL_connect:SSL negotiation finished successfully'),
    ('stderr', 'Verify return code: 0 (ok)'),
]


@pytest.fixture(autouse=True)
def prohibit_processes_and_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Offline classifier test attempted a process or socket connection')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)


def classify(lines, *, version='OpenSSL 3.0.2', code=0, timed_out=False, routing='split'):
    """Drive the real remote_entry callback using a process-free fake Runner."""
    class RecordedOutput:
        calls = 0
        command = None
        def identity(self):
            return dict(hostname='36f825bcc5da', uid=0)
        def tool_info(self):
            return version, '\n'.join(isolation.REQUIRED_FLAGS)
        def read_ca(self):
            return CA
        def probe(self, command, budget, callback):
            self.calls += 1
            self.command = command
            assert budget == 5.0
            for source, text in lines:
                if routing in ('stdout', 'stderr'):
                    source = routing
                # The transport delivers lines. Combined stream means same source,
                # not treating a whole multi-line transcript as one callback.
                for line in text.splitlines():
                    callback(source, line)
            return dict(returncode=code, timed_out=timed_out, elapsed_seconds=.12)
    runner = RecordedOutput()
    events = []
    result = isolation.remote_entry(runner, events.append, hashlib.sha256(CA).hexdigest())
    assert runner.calls == 1
    assert runner.command == [
        'openssl', 's_client', '-connect', '127.0.0.1:8000', '-servername', 'localhost',
        '-verify_hostname', 'localhost', '-verify_return_error', '-CAfile',
        '/root/.config/risk-agent/tls/ca.crt', '-brief', '-state', '-msg',
    ]
    return result, events


@pytest.mark.parametrize('routing', ['split', 'stdout', 'stderr'])
def test_actual_302_retained_trace_stays_inconclusive(routing):
    fixture = json.loads(FIXTURE.read_text())
    assert fixture['kind'].startswith('actual_retained')
    lines = [(row['stream'], row['text']) for row in fixture['lines']]
    result, _ = classify(lines, version=fixture['version'],
                         code=fixture['outcome']['returncode'],
                         timed_out=fixture['outcome']['timed_out'], routing=routing)
    assert result['category'] == fixture['historical_category'] == 'DIAGNOSTIC_INCONCLUSIVE'
    assert result['state']['verified'] and not result['state']['complete']
    # State names are not Finished message headers or an explicit completion report.
    assert not result['state']['finished_sent'] and not result['state']['finished_received']


@pytest.mark.parametrize('version', ['OpenSSL 3.0.2', 'OpenSSL 3.0.13'])
@pytest.mark.parametrize('routing', ['split', 'stdout', 'stderr'])
@pytest.mark.parametrize('style', ['legacy', 'brief'])
def test_synthetic_complete_formats_for_both_3x_version_labels(version, routing, style):
    messages = [
        ('stdout', '>>> TLS 1.3, Handshake [length 0132], ClientHello'),
        ('stdout', '<<< TLS 1.3, Handshake [length 007a], ServerHello'),
        ('stdout', '<<< TLS 1.3, Handshake [length 01ae], Certificate'),
        ('stdout', '<<< TLS 1.3, Handshake [length 0034], Finished'),
        ('stdout', '>>> TLS 1.3, Handshake [length 0034], Finished'),
    ]
    result, events = classify(messages + (LEGACY if style == 'legacy' else BRIEF),
                              version=version, routing=routing)
    assert result['category'] == 'REMOTE_TLS_SUCCESS'
    assert result['state']['complete']
    assert result['state']['finished_sent'] and result['state']['finished_received']
    assert any(e['event'] == 'tls_handshake_complete' for e in events)


@pytest.mark.parametrize('lines', [
    [('stderr', 'Verification: OK')],
    [('stdout', 'CONNECTED(00000003)')],
    [('stdout', '<<< TLS 1.3, Handshake [length 007a], ServerHello')],
    [('stderr', 'SSL_connect:SSLv3/TLS read finished'),
     ('stderr', 'SSL_connect:SSLv3/TLS write finished'), ('stderr', 'Verification: OK')],
    [('stdout', '<<< TLS 1.3, Handshake [length 0034], Finished'),
     ('stdout', '>>> TLS 1.3, Handshake [length 0034], Finished'), ('stderr', 'Verification: OK')],
    [('stdout', '>>> TLS 1.3, Alert [length 0002], warning close_notify'),
     ('stderr', 'Verification: OK')],
    [],
])
def test_exit_zero_and_partial_handshake_never_imply_completion(lines):
    result, _ = classify(lines)
    assert result['category'] == 'DIAGNOSTIC_INCONCLUSIVE'
    assert not result['state']['complete']


@pytest.mark.parametrize('missing', range(4))
def test_partial_brief_summary_cannot_succeed(missing):
    result, _ = classify(BRIEF[:missing] + BRIEF[missing + 1:])
    assert result['category'] == 'DIAGNOSTIC_INCONCLUSIVE'


@pytest.mark.parametrize('invalid', ['(NONE)', 'NONE', 'NULL', 'ECDHE-RSA-NULL-SHA'])
def test_null_or_absent_cipher_rejected(invalid):
    lines = [line if not line[1].startswith('Ciphersuite:') else ('stderr', f'Ciphersuite: {invalid}')
             for line in BRIEF]
    assert classify(lines)[0]['category'] == 'DIAGNOSTIC_INCONCLUSIVE'


@pytest.mark.parametrize('alert', [
    '<<< TLS 1.3, Alert [length 0002], fatal handshake_failure',
    'SSL3 alert read:fatal:handshake failure',
    'error:0A000410:SSL routines:ssl3_read_bytes:sslv3 alert handshake failure:SSL alert number 40',
])
def test_fatal_alert_blocks_even_complete_summary_with_zero_exit(alert):
    result, _ = classify(BRIEF + [('stderr', alert)])
    assert result['state']['fatal_alert']
    assert result['category'] == 'DIAGNOSTIC_INCONCLUSIVE'


def test_close_notify_is_not_a_fatal_alert_or_completion_marker():
    result, _ = classify(BRIEF + [('stdout', '>>> TLS 1.3, Alert [length 0002], warning close_notify')])
    assert result['category'] == 'REMOTE_TLS_SUCCESS'
    assert not result['state']['fatal_alert']


def test_tcp_connected_then_tls_timeout():
    result, _ = classify([('stdout', 'CONNECTED(00000003)'),
                          ('stdout', '>>> TLS 1.3, Handshake [length 0132], ClientHello')],
                         code=-15, timed_out=True)
    assert result['category'] == 'REMOTE_TLS_HANDSHAKE_TIMEOUT'


def test_explicit_certificate_failure_overrides_ok_and_completion():
    result, _ = classify(BRIEF + [('stderr', 'verify error:num=62:hostname mismatch')])
    assert result['category'] == 'REMOTE_CERTIFICATE_VERIFICATION_FAILURE'


@pytest.mark.parametrize('code,timed_out', [(1, False), (0, True), (-15, True)])
def test_completion_does_not_override_nonzero_exit_or_deadline(code, timed_out):
    assert classify(BRIEF, code=code, timed_out=timed_out)[0]['category'] != 'REMOTE_TLS_SUCCESS'


def test_summary_whitespace_crlf_and_independent_stream_order():
    result, _ = classify([('stdout', '  CONNECTION ESTABLISHED\r\n'),
                          ('stdout', ' Ciphersuite:   TLS_AES_256_GCM_SHA384\r\n'),
                          ('stderr', 'Verification:  OK\r\n'),
                          ('stderr', 'Protocol version:  TLSv1.3\r\n')])
    assert result['category'] == 'REMOTE_TLS_SUCCESS'


def test_unrelated_case_or_substring_is_not_an_exact_success_marker():
    result, _ = classify([('stderr', 'not CONNECTION ESTABLISHED'),
                          ('stderr', 'verification: OK'),
                          ('stderr', 'SSL_connect:failed before negotiation finished successfully')])
    assert result['category'] == 'DIAGNOSTIC_INCONCLUSIVE'


def test_filtered_output_preserves_completion_metadata_without_session_secrets():
    result, events = classify(BRIEF + [
        ('stdout', 'Master-Key: NEVER_STORE_THIS'),
        ('stdout', '    00 11 Session-Ticket NEVER_STORE_THIS'),
    ])
    assert result['category'] == 'REMOTE_TLS_SUCCESS'
    assert 'NEVER_STORE_THIS' not in json.dumps(events)
    assert {'tls_connection_established_summary','tls_protocol_summary','tls_ciphersuite_summary'} <= {
        e['event'] for e in events}


def test_serialized_remote_program_keeps_the_same_offline_classifier(monkeypatch):
    payload = isolation.remote_program()
    assert payload.endswith('remote_entry()\n')
    namespace = {}
    exec(payload[:-len('remote_entry()\n')], namespace)
    monkeypatch.setattr(isolation, 'remote_entry', namespace['remote_entry'])
    assert classify(BRIEF)[0]['category'] == 'REMOTE_TLS_SUCCESS'


def test_negotiated_parameters_without_established_marker_are_insufficient():
    result, _ = classify(BRIEF[1:])
    assert not result['state']['complete']
    assert result['category'] == 'DIAGNOSTIC_INCONCLUSIVE'
