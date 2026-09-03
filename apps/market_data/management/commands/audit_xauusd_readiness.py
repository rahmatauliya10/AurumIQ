"""Management command to audit XAUUSD persisted market data readiness."""
import os
import json
from django.core.management.base import BaseCommand, CommandError
from apps.market_data.readiness import XauUsdDataReadinessEvaluator


class Command(BaseCommand):
    help = "Audit actual persisted XAUUSD dataset quality and readiness for Phase 6 calibration."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output-manifest",
            type=str,
            default="artifacts/calibration/xauusd_data_manifest.json",
            help="Path to write machine-readable manifest JSON",
        )
        parser.add_argument(
            "--output-report",
            type=str,
            default="docs/calibration/XAUUSD_DATA_READINESS_REPORT.md",
            help="Path to write human-readable audit report Markdown",
        )
        parser.add_argument(
            "--baseline-sha",
            type=str,
            default="57f6de1405d0df8548182a166d245f1a3173363d",
            help="Authoritative main baseline Git SHA",
        )
        parser.add_argument(
            "--code-revision",
            type=str,
            default="HEAD",
            help="Current working branch Git SHA or ref",
        )
        parser.add_argument(
            "--allow-mutable-revision",
            action="store_true",
            help="Allow non-immutable code revisions (HEAD, branch names) during development.",
        )
        parser.add_argument(
            "--data-acquisition-code-revision",
            type=str,
            default="UNRESOLVED_PRECOMMIT_WORKTREE",
            help="Code revision active when the pilot dataset was acquired.",
        )
        parser.add_argument(
            "--expected-start",
            type=str,
            default="2020-04-07T00:00:00Z",
            help="Expected calibration start ISO timestamp (default: 2020-04-07T00:00:00Z)",
        )
        parser.add_argument(
            "--expected-end",
            type=str,
            default="2026-09-01T00:00:00Z",
            help="Expected calibration end ISO timestamp (default: 2026-09-01T00:00:00Z)",
        )
        parser.add_argument(
            "--no-coverage-check",
            action="store_true",
            help="Skip full historical coverage window check (useful for isolated candle quality checks).",
        )

    def handle(self, *args, **options):
        manifest_path = options["output_manifest"]
        report_path = options["output_report"]
        baseline_sha = options["baseline_sha"]
        code_rev = options["code_revision"]
        allow_mutable = options.get("allow_mutable_revision", False)
        acq_code_rev = options.get("data_acquisition_code_revision", "UNRESOLVED_PRECOMMIT_WORKTREE")
        expected_start_str = options["expected_start"]
        expected_end_str = options["expected_end"]
        no_coverage_check = options["no_coverage_check"]

        exp_start = None
        exp_end = None
        if not no_coverage_check:
            from apps.market_data.readiness import parse_strict_iso_datetime
            if expected_start_str:
                try:
                    exp_start = parse_strict_iso_datetime(expected_start_str)
                except Exception as e:
                    raise CommandError(f"INVALID_EXPECTED_START: {e}") from e
            if expected_end_str:
                try:
                    exp_end = parse_strict_iso_datetime(expected_end_str)
                except Exception as e:
                    raise CommandError(f"INVALID_EXPECTED_END: {e}") from e

        # Sealed manifest requires an immutable, non-empty explicit Git SHA
        if not allow_mutable:
            if not code_rev or code_rev.strip().upper() in ("HEAD", "MAIN", "MASTER") or len(code_rev.strip()) < 7:
                raise CommandError(
                    f"IMMUTABLE_CODE_REVISION_REQUIRED: A valid immutable Git commit SHA is required for sealed evidence artifacts, got '{code_rev}'. "
                    f"Literal 'HEAD' or branch names are forbidden unless --allow-mutable-revision is explicitly passed."
                )

        self.stdout.write("Auditing XAUUSD persisted dataset readiness...")
        report = XauUsdDataReadinessEvaluator.evaluate(
            expected_coverage_start=exp_start,
            expected_coverage_end=exp_end,
        )

        # Write manifest
        if manifest_path:
            os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
            with open(manifest_path, "w", encoding="utf-8") as f:
                manifest_dict = report.to_manifest_dict(
                    code_revision=code_rev,
                    data_acquisition_code_revision=acq_code_rev,
                    allow_mutable_revision=allow_mutable,
                )
                json.dump(manifest_dict, f, indent=2)
            self.stdout.write(self.style.SUCCESS(f"Manifest written to {manifest_path}"))

        # Write report
        if report_path:
            os.makedirs(os.path.dirname(report_path), exist_ok=True)
            with open(report_path, "w", encoding="utf-8") as f:
                report_md = report.to_markdown_report(
                    baseline_sha=baseline_sha,
                    code_revision=code_rev,
                    data_acquisition_code_revision=acq_code_rev,
                )
                f.write(report_md)
            self.stdout.write(self.style.SUCCESS(f"Audit report written to {report_path}"))

        self.stdout.write(f"Audit Complete. Decision: {report.decision} (Passed: {report.passed})")
