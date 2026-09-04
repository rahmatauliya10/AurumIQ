"""Clean-room reproducibility test (Prompt §10B).

Creates an isolated fresh SQLite database 'clean_room_test.sqlite3',
copies authoritative SourceSnapshots from the governing database,
runs bounded ingestion from zero (Run 1),
then reruns it (Run 2),
and asserts determinism, idempotency, and macro fingerprint invariance.
"""
import os
import sys
import shutil
import sqlite3
import datetime
from decimal import Decimal

# Configure Django settings before importing models
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
import django
django.setup()

from django.conf import settings
from django.core.management import call_command
from django.db import connections

from apps.market_data.models import (
    MacroEventIdentity,
    MacroScheduleVintage,
    MacroObservationVintage,
    SourceSnapshot,
)
from apps.market_data.macro.ingestion import ingest_xauusd_macro_evidence
from apps.market_data.macro.fingerprint import compute_macro_evidence_fingerprint


def run_clean_room_validation():
    test_db_path = os.path.abspath("clean_room_test.sqlite3")
    if os.path.exists(test_db_path):
        os.remove(test_db_path)

    print(f"1. Creating isolated test database at: {test_db_path}")

    # Switch Django default connection to test database
    old_db_name = settings.DATABASES["default"]["NAME"]
    settings.DATABASES["default"]["NAME"] = test_db_path
    connections["default"].close()

    try:
        print("2. Running migrations on isolated test database...")
        call_command("migrate", "--run-syncdb", verbosity=0)

        print("3. Pre-populating SourceSnapshot records from governed database...")
        # Read snapshots from governed database to ensure offline availability
        gov_conn = sqlite3.connect(old_db_name)
        test_conn = sqlite3.connect(test_db_path)

        gov_cur = gov_conn.cursor()
        test_cur = test_conn.cursor()

        gov_cur.execute("SELECT snapshot_id, source_url, source_name, first_retrieved_at, http_status, content_type, etag, last_modified_header, raw_payload_bytes_sha256, raw_content, created_at FROM market_data_sourcesnapshot")
        rows = gov_cur.fetchall()
        print(f"   Found {len(rows)} snapshots in governed database. Copying to test DB...")

        test_cur.executemany(
            "INSERT INTO market_data_sourcesnapshot (snapshot_id, source_url, source_name, first_retrieved_at, http_status, content_type, etag, last_modified_header, raw_payload_bytes_sha256, raw_content, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        test_conn.commit()
        gov_conn.close()
        test_conn.close()

        # Verify snapshots in test DB
        snap_count = SourceSnapshot.objects.count()
        print(f"   Test DB SourceSnapshot count: {snap_count}")
        assert snap_count > 0, "No snapshots found in test DB!"

        start_dt = datetime.datetime(2020, 4, 7, 0, 0, tzinfo=datetime.timezone.utc)
        end_dt = datetime.datetime(2026, 9, 1, 0, 0, tzinfo=datetime.timezone.utc)

        print("\n4. Executing RUN 1 (Bounded ingestion from zero)...")
        t0 = datetime.datetime.now()
        stats1 = ingest_xauusd_macro_evidence(start_dt=start_dt, end_dt=end_dt, dry_run=False)
        t1 = datetime.datetime.now()
        fp1 = compute_macro_evidence_fingerprint()
        sched_count_1 = MacroScheduleVintage.objects.count()
        obs_count_1 = MacroObservationVintage.objects.count()

        print(f"   Run 1 duration: {(t1 - t0).total_seconds():.2f}s")
        print(f"   Run 1 stats: {stats1.to_dict()}")
        print(f"   Run 1 schedule vintages: {sched_count_1}")
        print(f"   Run 1 observation vintages: {obs_count_1}")
        print(f"   Run 1 macro fingerprint: {fp1}")

        print("\n5. Executing RUN 2 (Idempotent rerun)...")
        t2 = datetime.datetime.now()
        stats2 = ingest_xauusd_macro_evidence(start_dt=start_dt, end_dt=end_dt, dry_run=False)
        t3 = datetime.datetime.now()
        fp2 = compute_macro_evidence_fingerprint()
        sched_count_2 = MacroScheduleVintage.objects.count()
        obs_count_2 = MacroObservationVintage.objects.count()

        print(f"   Run 2 duration: {(t3 - t2).total_seconds():.2f}s")
        print(f"   Run 2 stats: {stats2.to_dict()}")
        print(f"   Run 2 schedule vintages: {sched_count_2}")
        print(f"   Run 2 observation vintages: {obs_count_2}")
        print(f"   Run 2 macro fingerprint: {fp2}")

        print("\n6. Validating Clean-Room Assertions...")
        # Invariance and determinism checks
        assert fp1 == fp2, f"Fingerprint mismatch! Run 1: {fp1} != Run 2: {fp2}"
        print("   [PASS] Deterministic & Idempotent: Run 1 fingerprint == Run 2 fingerprint")

        assert sched_count_1 == sched_count_2, f"Schedule count mutated! Run 1: {sched_count_1} != Run 2: {sched_count_2}"
        print(f"   [PASS] Schedule vintage invariance: {sched_count_1} == {sched_count_2}")

        assert obs_count_1 == obs_count_2, f"Observation count mutated! Run 1: {obs_count_1} != Run 2: {obs_count_2}"
        print(f"   [PASS] Observation vintage invariance: {obs_count_1} == {obs_count_2}")

        assert stats2.schedule_vintages_inserted == 0, f"Run 2 inserted new schedules: {stats2.schedule_vintages_inserted}"
        assert stats2.observations_inserted == 0, f"Run 2 inserted new observations: {stats2.observations_inserted}"
        assert stats2.idempotent_skips > 0, "Run 2 reported 0 idempotent skips!"
        print(f"   [PASS] Run 2 strictly skipped existing records (skips: {stats2.idempotent_skips})")

        print("\n=======================================================")
        print("CLEAN-ROOM REPRODUCIBILITY TEST: ALL ASSERTIONS PASSED")
        print("=======================================================")
        print(f"Macro Evidence Fingerprint: {fp1}")
        print(f"Total Schedules: {sched_count_1}")
        print(f"Total Observations: {obs_count_1}")

    finally:
        # Restore settings and remove test DB
        settings.DATABASES["default"]["NAME"] = old_db_name
        connections["default"].close()
        if os.path.exists(test_db_path):
            try:
                os.remove(test_db_path)
                print(f"\nCleaned up test database: {test_db_path}")
            except Exception as e:
                print(f"Notice: could not remove test DB immediately: {e}")


if __name__ == "__main__":
    run_clean_room_validation()
