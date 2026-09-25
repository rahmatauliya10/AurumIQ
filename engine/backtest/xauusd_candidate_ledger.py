"""
AurumIQ XAUUSD Candidate Calibration Ledger.

Provides atomic, incremental, recoverable persistence of candidate evaluation
results during calibration runs.

Strict Non-Semantic Invariant:
Persistence does not alter evaluation or ranking logic.
A stored candidate is reused if and only if ALL provenance keys match exactly:
- candidate_id
- dataset_fingerprint
- code_revision
- selection_policy_fingerprint
- generator_policy_fingerprint
- cache_semantics_version
Otherwise it is recomputed.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


REQUIRED_PROVENANCE_KEYS = (
    "candidate_id",
    "dataset_fingerprint",
    "code_revision",
    "selection_policy_fingerprint",
    "generator_policy_fingerprint",
    "cache_semantics_version",
)


class XauUsdCandidateLedgerManager:
    """
    Manages atomic incremental candidate ledger persistence and strict provenance validation.
    """

    def __init__(
        self,
        ledger_path: Path,
        expected_provenance: Dict[str, str],
        schema: str = "aurumiq.calibration.candidate_ledger.v1",
    ):
        self.ledger_path = Path(ledger_path)
        self.expected_provenance = dict(expected_provenance)
        self.schema = schema
        self._entries: Dict[str, Dict[str, Any]] = {}
        self._load_existing()

    def _load_existing(self) -> None:
        """Load existing ledger entries if file exists."""
        if not self.ledger_path.exists():
            return

        try:
            with open(self.ledger_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            candidates = payload.get("candidates", [])
            for c in candidates:
                cand_id = c.get("candidate_id")
                if cand_id:
                    self._entries[cand_id] = c
        except Exception:
            # If corrupted or partial, fall back to empty
            self._entries = {}

    def can_reuse(self, candidate_id: str) -> bool:
        """
        Check if stored candidate can be safely reused based on exact provenance match.
        """
        if candidate_id not in self._entries:
            return False

        entry = self._entries[candidate_id]
        if entry.get("candidate_id") != candidate_id:
            return False

        for k in (
            "dataset_fingerprint",
            "code_revision",
            "selection_policy_fingerprint",
            "generator_policy_fingerprint",
            "cache_semantics_version",
        ):
            expected = self.expected_provenance.get(k)
            actual = entry.get(k)
            if not expected or actual != expected:
                return False

        # Ensure val_result payload or evaluation metrics exist
        if "eval_result" not in entry and "val_lcb_95" not in entry:
            return False

        return True

    def get_candidate_eval_result(self, candidate_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve stored evaluation result dictionary if provenance matches.
        """
        if not self.can_reuse(candidate_id):
            return None
        entry = self._entries[candidate_id]
        if "eval_result" in entry:
            return dict(entry["eval_result"])
        return None

    def record_candidate(
        self,
        record: Dict[str, Any],
        eval_result: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Atomically update the ledger with a candidate record and flush to disk.
        """
        cand_id = record["candidate_id"]
        # Attach eval_result for clean round-trip resume
        entry_to_store = dict(record)
        if eval_result is not None:
            entry_to_store["eval_result"] = eval_result

        self._entries[cand_id] = entry_to_store
        self._flush_atomic()

    def _flush_atomic(self) -> None:
        """Atomically flush current entries to ledger JSON file."""
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.ledger_path.with_suffix(".tmp")

        # Sort candidates deterministically by candidate index or ID
        sorted_candidates = sorted(
            self._entries.values(),
            key=lambda c: (c.get("candidate_index", 999), c.get("candidate_id", "")),
        )

        payload = {
            "schema": self.schema,
            "dataset_fingerprint": self.expected_provenance.get("dataset_fingerprint", ""),
            "code_revision": self.expected_provenance.get("code_revision", ""),
            "selection_policy_fingerprint": self.expected_provenance.get("selection_policy_fingerprint", ""),
            "generator_policy_fingerprint": self.expected_provenance.get("generator_policy_fingerprint", ""),
            "cache_semantics_version": self.expected_provenance.get("cache_semantics_version", ""),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "candidate_count": len(sorted_candidates),
            "candidates": sorted_candidates,
        }

        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")

        os.replace(tmp_path, self.ledger_path)

    @property
    def candidate_count(self) -> int:
        return len(self._entries)
