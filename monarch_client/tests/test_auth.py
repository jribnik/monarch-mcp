from __future__ import annotations

import json
import os
import subprocess
import time

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


class TestResolveReconBin:
    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("MONARCH_RECON_BIN", "/custom/recon")
        assert auth._resolve_recon_bin() == "/custom/recon"

    def test_falls_back_to_path(self, monkeypatch):
        monkeypatch.delenv("MONARCH_RECON_BIN", raising=False)
        monkeypatch.setattr(auth.shutil, "which", lambda name: "/usr/local/bin/recon")
        assert auth._resolve_recon_bin() == "/usr/local/bin/recon"

    def test_raises_when_nothing_found(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MONARCH_RECON_BIN", raising=False)
        monkeypatch.setattr(auth.shutil, "which", lambda name: None)
        # Redirect Path.home() so the hardcoded ~/src/api-recon/.venv/bin/recon
        # fallback resolves to a location that can't possibly exist, without
        # touching Path.exists globally.
        monkeypatch.setattr(auth.Path, "home", classmethod(lambda cls: tmp_path))
        with pytest.raises(MonarchAuthError, match="could not find"):
            auth._resolve_recon_bin()


class TestLoad:
    def test_fresh_cache_is_used_without_refresh(self, isolated_cache, monkeypatch):
        _write_cache(isolated_cache, cookies={"session_id": "fresh"})

        def _boom():
            raise AssertionError("should not refresh a fresh cache")

        monkeypatch.setattr(auth, "_run_recon_export", _boom)

        material = auth.load()
        assert material.cookies == {"session_id": "fresh"}
        assert material.headers == {"User-Agent": "test-ua"}

    def test_stale_cache_triggers_refresh(self, isolated_cache, monkeypatch):
        _write_cache(isolated_cache, cookies={"session_id": "stale"})
        old = time.time() - auth.AUTH_CACHE_MAX_AGE_SECONDS - 60
        os.utime(isolated_cache, (old, old))

        calls = []

        def _fake_refresh():
            calls.append(1)
            _write_cache(isolated_cache, cookies={"session_id": "refreshed"})

        monkeypatch.setattr(auth, "_run_recon_export", _fake_refresh)

        material = auth.load()
        assert calls == [1]
        assert material.cookies == {"session_id": "refreshed"}

    def test_missing_cache_triggers_refresh(self, isolated_cache, monkeypatch):
        assert not isolated_cache.exists()

        def _fake_refresh():
            _write_cache(isolated_cache, cookies={"session_id": "new"})

        monkeypatch.setattr(auth, "_run_recon_export", _fake_refresh)

        material = auth.load()
        assert material.cookies == {"session_id": "new"}

    def test_force_refresh_ignores_fresh_cache(self, isolated_cache, monkeypatch):
        _write_cache(isolated_cache, cookies={"session_id": "old"})
        calls = []

        def _fake_refresh():
            calls.append(1)
            _write_cache(isolated_cache, cookies={"session_id": "forced"})

        monkeypatch.setattr(auth, "_run_recon_export", _fake_refresh)

        material = auth.load(force_refresh=True)
        assert calls == [1]
        assert material.cookies == {"session_id": "forced"}

    def test_corrupt_cache_falls_through_to_refresh(self, isolated_cache, monkeypatch):
        isolated_cache.write_text("not json")

        def _fake_refresh():
            _write_cache(isolated_cache, cookies={"session_id": "recovered"})

        monkeypatch.setattr(auth, "_run_recon_export", _fake_refresh)

        material = auth.load()
        assert material.cookies == {"session_id": "recovered"}

    def test_refresh_failure_raises_auth_error_naming_the_fix(
        self, isolated_cache, monkeypatch
    ):
        def _fake_refresh():
            raise MonarchAuthError("boom\nrun: recon login monarch")

        monkeypatch.setattr(auth, "_run_recon_export", _fake_refresh)

        with pytest.raises(MonarchAuthError, match="recon login monarch"):
            auth.load()


class TestRunReconExport:
    def test_nonzero_exit_raises_with_stderr(self, isolated_cache, monkeypatch):
        monkeypatch.setattr(auth, "_resolve_recon_bin", lambda: "/fake/recon")

        def _fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args, returncode=1, stdout="", stderr="session expired"
            )

        monkeypatch.setattr(auth.subprocess, "run", _fake_run)

        with pytest.raises(MonarchAuthError, match="session expired"):
            auth._run_recon_export()

    def test_timeout_raises_auth_error(self, isolated_cache, monkeypatch):
        monkeypatch.setattr(auth, "_resolve_recon_bin", lambda: "/fake/recon")

        def _fake_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="recon", timeout=30)

        monkeypatch.setattr(auth.subprocess, "run", _fake_run)

        with pytest.raises(MonarchAuthError, match="timed out"):
            auth._run_recon_export()

    def test_exports_the_currently_selected_site(self, isolated_cache, monkeypatch):
        """Regression test for the incident this module's docstring
        describes: the site actually passed to `recon export-session` must
        track MONARCH_CLIENT_SITE, not a hardcoded 'monarch'."""
        monkeypatch.setenv("MONARCH_CLIENT_SITE", "monarch-sandbox")
        monkeypatch.setattr(auth, "_resolve_recon_bin", lambda: "/fake/recon")

        captured_cmd = []

        def _fake_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(auth.subprocess, "run", _fake_run)

        auth._run_recon_export()

        assert "export-session" in captured_cmd
        assert "monarch-sandbox" in captured_cmd
        assert "monarch" not in captured_cmd  # not even as a substring match via the real site
        out_idx = captured_cmd.index("--out")
        assert "monarch-sandbox" in captured_cmd[out_idx + 1]


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

        def _fake_refresh():
            _write_cache(auth.cache_path(), cookies={"session_id": "sandbox-session"})

        monkeypatch.setattr(auth, "_run_recon_export", _fake_refresh)

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
