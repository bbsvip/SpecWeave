"""Regression tests for OAuth diagnostics, Fernet validation and UI rendering."""
import os
import sys
import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'source'))
from test_app import client as client_fixture  # reuse isolated test DB / app lifecycle
client = client_fixture


def test_fernet_configuration_fails_early_and_without_leaking(monkeypatch):
    from security import validate_encryption_key
    monkeypatch.setenv('ENCRYPTION_KEY', 'ordinary-password-should-never-be-used')
    with pytest.raises(RuntimeError, match='ENCRYPTION_KEY không hợp lệ') as result:
        validate_encryption_key()
    assert 'ordinary-password' not in str(result.value)
    monkeypatch.setenv('ENCRYPTION_KEY', Fernet.generate_key().decode())
    cipher = validate_encryption_key()
    assert cipher.decrypt(cipher.encrypt(b'safe')) == b'safe'


def test_project_trash_active_nav_and_json_profile(client):
    from test_app import auth, create_project
    from db import Project, SessionLocal
    csrf=auth(client)
    pid=create_project(client,csrf)
    response=client.get('/projects/trash')
    assert response.status_code==200
    assert 'href="/projects/trash" class="side-link active"' in response.text
    assert 'href="/projects" class="side-link active"' not in response.text
    with SessionLocal() as db:
        pr=db.get(Project,pid)
        pr.profile={**pr.profile, 'nested_data':{'criteria':['one','two'], 'max':4}}
        db.commit()
    response=client.get(f'/projects/{pid}')
    assert response.status_code==200
    assert 'Xem cấu hình JSON' in response.text
    assert '&#34;criteria&#34;' in response.text or '&quot;criteria&quot;' in response.text or '"criteria"' in response.text
    assert "{'criteria':" not in response.text


def test_json_file_editor_has_formatter(client):
    from test_app import auth, create_project
    csrf=auth(client)
    pid=create_project(client, csrf)
    response=client.get(f'/projects/{pid}/files')
    assert 'id="formatFileJson"' in response.text
