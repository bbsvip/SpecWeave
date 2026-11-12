"""The OpenAI login preflight never exposes raw error text or credentials."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'source'))
from openai_oauth_runtime import safe_event


def test_oauth_missing_device_id_has_safe_diagnostics():
    event = safe_event({
        'type':'error','code':'OAUTH_DEVICE_ID','phase':'device_identity',
        'hint':'DEVICE_ID_MISSING', 'secret':'SENSITIVE_TOKEN',
    })
    assert event['code']=='OAUTH_DEVICE_ID'
    assert event['phase']=='device_identity'
    assert event['hint']=='DEVICE_ID_MISSING'
    assert 'SENSITIVE_TOKEN' not in repr(event)


def test_oauth_invalid_pi_settings_has_safe_diagnostics():
    event = safe_event({
        'type':'error','code':'OAUTH_SETTINGS_INVALID','phase':'device_identity',
        'hint':'PI_SETTINGS_INVALID', 'url':'https://auth.openai.com/secret',
    })
    assert event['code']=='OAUTH_SETTINGS_INVALID'
    assert 'secret' not in repr(event)


def test_oauth_identity_progress_does_not_echo_untrusted_message():
    assert safe_event({'type':'progress','message':'some private token'}) == {
        'type':'progress','message':'OpenAI sign-in in progress'
    }
    assert safe_event({'type':'progress','message':'Pi installation identity ready'}) == {
        'type':'progress','message':'Pi installation identity ready'
    }
