from __future__ import annotations

import json
import os

import pytest

from monarch_client import auth
from monarch_client.errors import MonarchAuthError


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """Every test gets its own state dir (so nothing touches the real
    ~/.monarch-mcp/api-auth.*.json) and a clean MONARCH_CLIENT_SITE/
    _announced_site, since the "announce once per site per process" cache
    would otherwise leak between tests."""
    monkeypatch.setattr(auth, "STATE_DIR", tmp_path)
    monkeypatch.delenv("MONARCH_CLIENT_SITE", raising=False)
    monkeypatch.setattr(auth, "_announced_site", None)
    return auth.cache_path()


def _write_cache(path, *, cookies=None, headers=None, expires_at=None, exported_at=None):
    payload = {
        "site": "monarch",
        "api_host": auth.API_HOST,
        "cookies": cookies or {"session_id": "abc"},
        "headers": headers or {"User-Agent": "test-ua"},
        "expires_at": expires_at,
        "exported_at": exported_at or "2026-01-01T00:00:00+00:00",
    }
    path.write_text(json.dumps(payload))
    os.chmod(path, 0o600)


class TestLoad:
    """auth.py is a pure file reader -- no subprocess, no refresh, no
    `recon` binary knowledge at all. See auth.py's module docstring for
    why: an earlier version shelled out to `recon export-session` on a
    staleness heuristic, making a long-running MCP server depend on a
    working api-recon checkout indefinitely."""

    def test_reads_existing_cache(self, isolated_cache):
        _write_cache(isolated_cache, cookies={"session_id": "abc"})
        material = auth.load()
        assert material.cookies == {"session_id": "abc"}
        assert material.headers == {"User-Agent": "test-ua"}

    def test_missing_file_raises_with_exact_export_command(self, isolated_cache):
        assert not isolated_cache.exists()
        with pytest.raises(MonarchAuthError) as exc_info:
            auth.load()
        message = str(exc_info.value)
        assert "recon export-session monarch --api-host api.monarch.com" in message
        assert str(isolated_cache) in message

    def test_corrupt_file_raises_naming_the_fix(self, isolated_cache):
        isolated_cache.write_text("not json")
        with pytest.raises(MonarchAuthError, match="recon export-session"):
            auth.load()

    def test_malformed_file_missing_cookies_key_raises(self, isolated_cache):
        isolated_cache.write_text(json.dumps({"headers": {}}))
        with pytest.raises(MonarchAuthError, match="malformed"):
            auth.load()

    def test_never_shells_out(self, isolated_cache, monkeypatch):
        """There is no subprocess-capable hook left in this module at all --
        this test exists to catch a regression that reintroduces one."""
        _write_cache(isolated_cache, cookies={"session_id": "abc"})
        assert not hasattr(auth, "subprocess")
        assert not hasattr(auth, "_run_recon_export")
        assert not hasattr(auth, "_resolve_recon_bin")
        auth.load()  # should succeed via a plain file read alone


class TestSiteIsolation:
    def test_default_site_is_the_real_account(self, monkeypatch):
        monkeypatch.delenv("MONARCH_CLIENT_SITE", raising=False)
        assert auth.site() == "monarch"

    def test_env_var_overrides_site(self, monkeypatch):
        monkeypatch.setenv("MONARCH_CLIENT_SITE", "monarch-sandbox")
        assert auth.site() == "monarch-sandbox"

    def test_real_and_sandbox_caches_are_distinct_files(self, isolated_cache, monkeypatch):
        monkeypatch.delenv("MONARCH_CLIENT_SITE", raising=False)
        real_path = auth.cache_path()

        monkeypatch.setenv("MONARCH_CLIENT_SITE", "monarch-sandbox")
        sandbox_path = auth.cache_path()

        assert real_path != sandbox_path
        assert "monarch-sandbox" in sandbox_path.name
        assert "monarch-sandbox" not in real_path.name

    def test_loading_sandbox_never_touches_real_cache_file(self, isolated_cache, monkeypatch):
        monkeypatch.delenv("MONARCH_CLIENT_SITE", raising=False)
        real_path = auth.cache_path()
        _write_cache(real_path, cookies={"session_id": "REAL-ACCOUNT-DO-NOT-TOUCH"})

        monkeypatch.setenv("MONARCH_CLIENT_SITE", "monarch-sandbox")
        _write_cache(auth.cache_path(), cookies={"session_id": "sandbox-session"})

        material = auth.load()
        assert material.cookies == {"session_id": "sandbox-session"}
        # The real account's cache file is byte-for-byte untouched.
        real_payload = json.loads(real_path.read_text())
        assert real_payload["cookies"] == {"session_id": "REAL-ACCOUNT-DO-NOT-TOUCH"}

    def test_load_announces_site_once_per_process(self, isolated_cache, monkeypatch, capsys):
        _write_cache(isolated_cache, cookies={"session_id": "x"})

        auth.load()
        auth.load()  # second call, same site -- no repeat announcement
        err = capsys.readouterr().err
        assert err.count("authenticating against site") == 1
        assert "'monarch'" in err
        assert "REAL account" in err

    def test_load_re_announces_on_site_change(self, isolated_cache, monkeypatch, capsys):
        _write_cache(isolated_cache, cookies={"session_id": "x"})
        auth.load()

        monkeypatch.setenv("MONARCH_CLIENT_SITE", "monarch-sandbox")
        _write_cache(auth.cache_path(), cookies={"session_id": "y"})
        auth.load()

        err = capsys.readouterr().err
        assert err.count("authenticating against site") == 2
        assert "sandbox/test account" in err
