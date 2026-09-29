"""
python -m monarch_client.operations --provenance-json

Prints a JSON array describing every vendored operation, for external
drift checkers (api-recon's nightly drift-watch) to compare against their
live catalog WITHOUT reaching into this repo's files or importing
monarch_client. This is the contract between the two repos:

    [{"name": str,                       # operation name
      "vendored_path": str,              # repo-relative path to its .graphql
      "catalog_query_hash": str | null,  # hash recorded at vendoring time;
                                         #   null = hand-picked subset / hand-
                                         #   recovered, never catalog-exported
                                         #   -- the checker must skip these
      "walk_reachable": bool}, ...]      # false = the read-only walk can never
                                         #   re-observe it (mutations, etc.);
                                         #   the checker must skip, not warn

Sorted by name so the output is stable. Reads only PROVENANCE -- no
network, no auth, no files outside this package.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import PROVENANCE


def provenance_records() -> list[dict]:
    return [
        {
            "name": name,
            "vendored_path": f"monarch_client/operations/{name}.graphql",
            "catalog_query_hash": entry.get("catalog_query_hash"),
            "walk_reachable": bool(entry.get("walk_reachable", True)),
        }
        for name, entry in sorted(PROVENANCE.items())
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--provenance-json",
        action="store_true",
        required=True,
        help="print every vendored op's drift-check record as a JSON array",
    )
    parser.parse_args(argv)
    json.dump(provenance_records(), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
