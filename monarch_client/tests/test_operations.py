from __future__ import annotations

import hashlib

import pytest

from monarch_client import operations
from monarch_client.errors import VendoredOperationError


@pytest.mark.parametrize("op_name", sorted(operations.PROVENANCE.keys()))
def test_vendored_file_matches_recorded_hash(op_name):
    """Catches a hand-edit to a .graphql file that wasn't re-recorded in
    PROVENANCE."""
    text = operations.load(op_name)
    actual = hashlib.sha256(text.encode("utf-8")).hexdigest()
    expected = operations.PROVENANCE[op_name]["vendored_sha256"]
    assert actual == expected


@pytest.mark.parametrize("op_name", sorted(operations.PROVENANCE.keys()))
def test_no_unexplained_redaction_placeholder(op_name):
    """A literal that survived as "__recon_redacted__" and wasn't flagged
    hand_repaired would silently send a broken query at runtime."""
    text = operations.load(op_name)
    entry = operations.PROVENANCE[op_name]
    if "__recon_redacted__" in text:
        assert entry["hand_repaired"], (
            f"{op_name} contains an unrepaired redaction placeholder"
        )


@pytest.mark.parametrize("op_name", sorted(operations.PROVENANCE.keys()))
def test_verify_integrity_passes_for_every_vendored_op(op_name):
    operations.verify_integrity(op_name)  # raises on mismatch


def test_verify_integrity_catches_tampering(monkeypatch):
    monkeypatch.setitem(
        operations._cache, "Common_GetMe", "query Common_GetMe { me { id } }\n"
    )
    with pytest.raises(VendoredOperationError, match="has changed since"):
        operations.verify_integrity("Common_GetMe")


def test_load_raises_for_unvendored_op():
    with pytest.raises(VendoredOperationError, match="no vendored query text"):
        operations.load("Nonexistent_Op_Name")


def test_verify_integrity_is_noop_for_unrecorded_op():
    operations.verify_integrity("Some_Op_Nobody_Vendored_Or_Recorded")


def _graphql_files_on_disk() -> set[str]:
    from pathlib import Path

    return {p.stem for p in Path(operations.__file__).parent.glob("*.graphql")}


def test_every_graphql_file_on_disk_has_a_provenance_entry():
    """Reverse of the hash test above: a .graphql file nobody recorded would
    otherwise be sent with no integrity check and no drift coverage."""
    missing = _graphql_files_on_disk() - set(operations.PROVENANCE)
    assert not missing, f"vendored files with no PROVENANCE entry: {sorted(missing)}"


def test_every_provenance_entry_has_a_file_and_a_hash():
    assert set(operations.PROVENANCE) <= _graphql_files_on_disk()
    for name, entry in operations.PROVENANCE.items():
        assert entry.get("vendored_sha256"), f"{name} has no vendored_sha256"


def test_verify_integrity_rejects_file_with_no_provenance(monkeypatch):
    """Simulate a new .graphql dropped in without a PROVENANCE entry."""
    monkeypatch.setitem(operations._cache, "Brand_New_Op", "query Brand_New_Op { me { id } }\n")
    with pytest.raises(VendoredOperationError, match="no PROVENANCE entry"):
        operations.verify_integrity("Brand_New_Op")
