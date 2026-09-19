"""Freeze each error-scope attempt's protocol before its real requests.

Both attempts reuse the previous round's frozen file set, split, seed and
gate values. Only the source hashes and the run directory differ, because
each attempt tests a reviewed fix rather than a re-selection:

- attempt 1: MATERIALITY grounding rule 6 (conflicted observations).
- attempt 2: the same rule plus the recall scanner's output budget, whose
  8192-token ceiling truncated a real scan in attempt 1.

Attempt 1's protocol is a post-hoc reconstruction: its original file was
overwritten by an earlier revision of this script (which re-derived hashes
from the current tree after attempt 2's config edit). The reconstruction
takes every hash from the optimization-round baseline and overrides only
``detector/semantics.py`` with its current value, which is valid because
that file has not changed since attempt 1 ran; ``detector/configs/autodl.json``
keeps the pre-fix hash. The archived attempt-1 records corroborate the
reconstruction: the scan that failed reported ``finish_reason=length`` at
8192 completion tokens, which the reconstructed configuration can produce
and the current 32768 ceiling cannot.
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent

BASELINE = "reports/optimization_20260918/protocol.json"

# Files whose frozen hash is taken from the current tree, per attempt. Every
# other file takes the baseline hash; the script asserts the baseline still
# matches the tree for those, so a stale baseline cannot pass silently.
ATTEMPTS = {
    "attempt1_semantics_fix": {
        "from_current_tree": ["detector/semantics.py"],
        "previous": BASELINE,
        "summary": (
            "MATERIALITY grounding rule 6 (conflicted observations are not "
            "settled by the record) plus the judge's step-3 application "
            "note; see docs/error-scope.md."
        ),
        "reconstruction": (
            "Post-hoc reconstruction from the optimization-round baseline "
            "with detector/semantics.py overridden at its current hash; "
            "detector/configs/autodl.json keeps the pre-fix (8192) hash, "
            "which the archived attempt-1 scan corroborates via "
            "finish_reason=length at 8192 completion tokens."
        ),
        # Files this attempt did not test but which the tree changed after it
        # ran; each must be exactly a file the next attempt pins, so drift is
        # always accounted for by a later recorded change.
        "known_post_attempt_changes": [],
        "tree_validated": False,
    },
    "attempt2_recall_budget_fix": {
        "from_current_tree": [
            "detector/semantics.py",
        ],
        "previous": "reports/error_scope_20260918/attempts/attempt1_semantics_fix/protocol.json",
        "summary": (
            "Attempt 1's rule 6 fix, plus raising the recall scanner's "
            "max_tokens from 8192 to 32768. In attempt 1 the scanner's reply "
            "was cut at the 8192 ceiling (finish_reason=length), which is a "
            "provider error rather than a contract violation, so the stage "
            "read as unavailable and degraded an authoritative clean verdict "
            "to degraded_judge_only. No gate, split, seed or label changed."
        ),
        "reconstruction": "",
        "known_post_attempt_changes": [],
        "tree_validated": False,
    },
    "attempt3_cache_path_fix": {
        "from_current_tree": [
            "detector/semantics.py",
            "evolution/backends.py",
            "evolution/tests/test_concurrent_cache.py",
        ],
        "previous": "reports/error_scope_20260918/attempts/attempt2_recall_budget_fix/protocol.json",
        "summary": (
            "Attempts 1-2's fixes, plus bounding the detector-stage cache "
            "file name so an evidence-tagged case cannot exceed the Windows "
            "path ceiling. In attempt 2 the preflight passed (2 TP + 2 TN), "
            "and the run then froze at the defect-injection audit because "
            "one gain case, OpenDeepSearch/080, failed its cache write with "
            "WinError 3 at a 268-character path and fail-closed to "
            "unresolved, which the audit must not certify as a gate "
            "rejection. The cache name now degrades to a digest past the "
            "budget. No gate, split, seed or label changed."
        ),
        "reconstruction": "",
        "known_post_attempt_changes": [],
        "tree_validated": True,
    },
    "attempt4_runtime_envelope_fix": {
        "from_current_tree": [
            "detector/semantics.py",
            "evolution/backends.py",
            "evolution/tests/test_concurrent_cache.py",
            "tests/test_repository.py",
        ],
        "previous": "reports/error_scope_20260918/attempts/attempt3_cache_path_fix/protocol.json",
        "summary": (
            "Attempts 1-3's fixes, plus runtime envelopes sized from a "
            "measurement instead of a guess. Measured with "
            "scripts/diagnostic_019_timing.py: SmolAgents/019 needs 1480 s "
            "end to end with generous per-call budgets. At the 900 s case "
            "deadline its judge votes consumed the whole budget with two "
            "480 s call timeouts, the tracer and defense never started, and "
            "the row resolved non-authoritative, which froze the "
            "defect-injection audit because it must not certify a gate "
            "rejection it could not score. Raised: case deadline 900->3600, "
            "per-attempt 240->300, per-call total 480->900, and (in this "
            "attempt's config only) the cycle deadline 7200->14400, since "
            "that case appears in three sequential arm evaluations per "
            "cycle. These are fail-closed bounding envelopes: they can only "
            "convert an unmeasurable row into a measured one and cannot "
            "change any admission threshold, gate, split, seed or label."
        ),
        "reconstruction": "",
        "known_post_attempt_changes": [],
        "tree_validated": True,
        # The cycle deadline lives under "budget"; the audit must report the
        # deviation instead of asserting budget equality.
        "budget_envelope_overrides": ["cycle_timeout_seconds"],
    },
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _key(path: str) -> str:
    """Repo-relative path in the frozen manifest's separator convention."""
    return path.replace("/", "\\")


def freeze(attempt: str, spec: dict) -> dict:
    attempt_dir = OUT / "attempts" / attempt
    config_path = attempt_dir / "run_config.json"
    assert config_path.is_file(), config_path
    baseline = json.loads((ROOT / BASELINE).read_text(encoding="utf-8"))
    overrides = {
        _key(path): sha(ROOT / path)
        for path in spec["from_current_tree"]
    }
    # The detector config is pinned from this attempt's frozen copy: later
    # attempts legitimately change runtime envelopes, so re-deriving an
    # earlier attempt's config from the working tree would misreport what it
    # ran. Each frozen copy is byte-exact (verified against the baseline
    # protocol hash for attempt 1).
    frozen_config = attempt_dir / "frozen/detector-config-autodl.json"
    assert frozen_config.is_file(), frozen_config
    hashes = dict(baseline["source_sha256"])
    hashes[_key("detector/configs/autodl.json")] = sha(frozen_config)
    hashes.update(overrides)
    # Every file that keeps its baseline hash must still be at that hash now,
    # unless it is a declared post-attempt change. Otherwise the baseline is
    # stale and the freeze would be fiction.
    excused = {_key(path) for path in spec.get("known_post_attempt_changes") or ()}
    if spec.get("tree_validated"):
        # Only the newest attempt can be validated against the tree: a later
        # attempt legitimately changes files an earlier one froze, so
        # re-deriving an earlier protocol can no longer claim to match the
        # working tree. Historical attempts are corroborated by their archived
        # runs instead, and this flag records which check applied.
        drifted = sorted(
            path for path, digest in hashes.items()
            if path not in overrides and path not in excused
            and path != _key("detector/configs/autodl.json")
            and sha(ROOT / path) != digest
        )
        assert not drifted, f"baseline drifted for {drifted}"
    unexplained = sorted(
        path for path in excused
        if path in overrides or path not in hashes
    )
    assert not unexplained, f"declared post-attempt change is not a real override: {unexplained}"
    corpus = {
        path: sha(ROOT / path)
        for path in sorted(
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "reports/optimization_20260918/corpus").rglob("*")
            if path.is_file()
        )
    }
    previous = json.loads((ROOT / spec["previous"]).read_text(encoding="utf-8"))
    # An attempt may pin a file outside the previous manifest (for example a
    # repository test that now locks a shipped budget). Those additions are
    # listed as new coverage rather than silently compared against nothing.
    added = sorted(path for path in hashes if path not in previous["source_sha256"])
    changed = sorted(
        path for path, digest in hashes.items()
        if path in previous["source_sha256"]
        and digest != previous["source_sha256"][path]
    )
    protocol = {
        "config": f"reports/error_scope_20260918/attempts/{attempt}/run_config.json",
        "split": "original frozen mini S=6 G=4",
        "seed": 20260824,
        "cycles_if_preflight_passes": 2,
        "stop_rule": (
            "First blocked preflight stops the experiment; no diagnostic "
            "fallback, reseed, threshold change or retry selection. Diagnostic "
            "coverage is informational only."
        ),
        "attempt": attempt,
        "previous_protocol": spec["previous"],
        "change_under_test": {
            "files": sorted(set(changed) | set(overrides)),
            "changed_since_previous_protocol": changed,
            "declared_by_attempt": sorted(overrides),
            "summary": spec["summary"],
        },
        "added_to_manifest": added,
        "reconstruction": spec["reconstruction"],
        "known_post_attempt_changes": sorted(spec.get("known_post_attempt_changes") or ()),
        "tree_validated": bool(spec.get("tree_validated")),
        "budget_envelope_overrides": sorted(spec.get("budget_envelope_overrides") or ()),
        "frozen_detector_config": str(frozen_config.relative_to(ROOT).as_posix()),
        "source_sha256": hashes,
        "corpus_files_sha256": corpus,
    }
    (attempt_dir / "protocol.json").write_text(
        json.dumps(protocol, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"attempt": attempt, "change_under_test": changed,
            "frozen_files": len(hashes), "corpus_files": len(corpus)}


def main() -> None:
    for attempt, spec in ATTEMPTS.items():
        print(json.dumps(freeze(attempt, spec), ensure_ascii=False))


if __name__ == "__main__":
    main()
