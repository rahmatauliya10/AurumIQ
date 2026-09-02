"""Management command to audit XAUUSD persisted market data readiness."""
import os
import json
from django.core.management.base import BaseCommand
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
        expected_start_str = options["expected_start"]
        expected_end_str = options["expected_end"]
        no_coverage_check = options["no_coverage_check"]

        exp_start = None
        exp_end = None
        if not no_coverage_check:
            from datetime import datetime, timezone
            if expected_start_str:
                exp_start = datetime.fromisoformat(expected_start_str.replace("Z", "+00:00")).astimezone(timezone.utc)
            if expected_end_str:
                exp_end = datetime.fromisoformat(expected_end_str.replace("Z", "+00:00")).astimezone(timezone.utc)

        self.stdout.write("Auditing XAUUSD persisted dataset readiness...")
        report = XauUsdDataReadinessEvaluator.evaluate(
            expected_coverage_start=exp_start,
            expected_coverage_end=exp_end,
        )

        # Write manifest
        if manifest_path:
            os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(report.to_manifest_dict(code_revision=code_rev), f, indent=2)
            self.stdout.write(self.style.SUCCESS(f"Manifest written to {manifest_path}"))

        # Write report
        if report_path:
            os.makedirs(os.path.dirname(report_path), exist_ok=True)
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(report.to_markdown_report(baseline_sha=baseline_sha, code_revision=code_rev))
            self.stdout.write(self.style.SUCCESS(f"Audit report written to {report_path}"))

        self.stdout.write(f"Audit Complete. Decision: {report.decision} (Passed: {report.passed})")
