"""Exclusive, verified legacy-storage migration. No camera access. Cleanup is explicit."""

import argparse
import json
import sqlite3
from pathlib import Path

from uap_observer.evidence.files import atomic_write, canonical, digest
from uap_observer.evidence.migrate import migrate_legacy, remove_verified_redundancy
from uap_observer.evidence.service import EvidenceService
from uap_observer.evidence.verify import verify_package
from uap_observer.storage.lease import DataLease
from uap_observer.storage.ledger import Ledger


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--remove-verified-redundancy", action="store_true")
    args = parser.parse_args()
    data = args.data.resolve()
    report = args.report.resolve()
    report.mkdir(parents=True, exist_ok=True)
    lease = DataLease(data)
    try:
        raw = sqlite3.connect(data / "ledger.sqlite")
        if raw.execute(
            "SELECT count(*) FROM sessions WHERE state IN ('running','connecting')"
        ).fetchone()[0]:
            raise RuntimeError("capture must stop before migration")
        backup = report / "before-storage-migration.sqlite"
        if not backup.exists():
            with sqlite3.connect(backup) as target:
                raw.backup(target)
        raw.close()
        ledger = Ledger(data / "ledger.sqlite")
        # Existing data exceeds the future budget; this bounded migration never captures.
        service = EvidenceService(ledger, data / "evidence", total_budget_bytes=None)

        def progress(value):
            print(json.dumps(value), flush=True)

        inventory_path = report / "inventory.json"
        if args.remove_verified_redundancy:
            plan = json.loads(inventory_path.read_bytes())
            for export in ledger.query("SELECT path,sha256 FROM exports"):
                if (
                    digest(Path(export["path"])) != export["sha256"]
                    or not verify_package(Path(export["path"]))["verified"]
                ):
                    raise ValueError("preserved export failed verification")
            result = remove_verified_redundancy(service, plan, progress)
            atomic_write(report / "cleanup.json", canonical(result))
            progress(result)
        else:
            before = service.budget.usage()
            plan = migrate_legacy(service, progress)
            plan["before_usage"] = before
            plan["backup_sha256"] = digest(backup)
            # Verify existing ZIPs and a fresh standalone export from shared payloads.
            for export in ledger.query("SELECT path,sha256 FROM exports"):
                if (
                    digest(Path(export["path"])) != export["sha256"]
                    or not verify_package(Path(export["path"]))["verified"]
                ):
                    raise ValueError("existing export integrity failure")
            chosen = ledger.query(
                "SELECT id FROM events WHERE manifest_path LIKE '%storage-v2.json' LIMIT 1"
            )
            if not chosen:
                chosen = ledger.query("SELECT id FROM events LIMIT 1")
            package = service.export(chosen[0]["id"])
            verification = verify_package(package)
            if not verification["verified"]:
                raise ValueError("shared standalone export failed verification")
            plan["verification_export"] = {
                "path": str(package),
                "sha256": digest(package),
                "verification": verification,
            }
            atomic_write(inventory_path, canonical(plan))
            progress(
                {k: v for k, v in plan.items() if k not in ("inventory", "corrections")}
            )
    finally:
        lease.close()


if __name__ == "__main__":
    main()
