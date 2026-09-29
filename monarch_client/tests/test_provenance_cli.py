"""
The `python -m monarch_client.operations --provenance-json` contract that
api-recon's drift-watch consumes (see operations/__main__.py's docstring).
Run as a real subprocess so the `-m` entrypoint itself is exercised.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from monarch_client import doctor, operations

REPO_ROOT = Path(__file__).resolve().parents[2]
KEYS = {"name", "vendored_path", "catalog_query_hash", "walk_reachable"}


def _run() -> list[dict]:
    proc = subprocess.run(
        [sys.executable, "-m", "monarch_client.operations", "--provenance-json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_one_record_per_vendored_op_with_exact_schema():
    records = _run()
    assert [r["name"] for r in records] == sorted(operations.PROVENANCE)
    for r in records:
        assert set(r) == KEYS
        assert isinstance(r["walk_reachable"], bool)
        assert r["catalog_query_hash"] is None or len(r["catalog_query_hash"]) == 64


def test_every_vendored_path_exists():
    for r in _run():
        assert (REPO_ROOT / r["vendored_path"]).is_file(), r["vendored_path"]


def test_flags_match_provenance():
    by_name = {r["name"]: r for r in _run()}
    for name, entry in operations.PROVENANCE.items():
        assert by_name[name]["catalog_query_hash"] == entry.get("catalog_query_hash")
        # A mutation-kind op is never walk-reachable, whatever PROVENANCE says
        # (the hand-recovered mutations carry no flag at all).
        expected = entry.get("walk_reachable", True) and not operations.is_mutation(name)
        assert by_name[name]["walk_reachable"] == expected


def test_every_mutation_reports_not_walk_reachable():
    """Including the three hand-recovered mutations with no PROVENANCE flag,
    which used to default to walk_reachable=true."""
    by_name = {r["name"]: r for r in _run()}
    mutations = [n for n in operations.PROVENANCE if operations.is_mutation(n)]
    assert len(mutations) >= 10, mutations
    for name in mutations:
        assert by_name[name]["walk_reachable"] is False, name
    for name in (
        "Common_DeleteTransactionMutation",
        "Common_DeleteTransactionRule",
        "Common_MarkAsNotRecurring",
    ):
        assert by_name[name]["walk_reachable"] is False, name


def test_null_hash_ops_are_present_and_identifiable():
    """The three hand-recovered mutations have no catalog hash; the checker
    is told so (null) rather than having them omitted."""
    nulls = {r["name"] for r in _run() if r["catalog_query_hash"] is None}
    assert {
        "Common_DeleteTransactionMutation",
        "Common_DeleteTransactionRule",
        "Common_MarkAsNotRecurring",
    } <= nulls


def test_doctor_no_longer_reaches_into_api_recon():
    assert not hasattr(doctor, "_check_catalog_drift")
    assert not hasattr(doctor, "_api_recon_catalog_path")
