"""Offline probe of the causal candidate-ordering layer.

Measures what the detector can *read* before any model is consulted: the
signal-priority distribution, the gold-coverage curve of ``top_k``, the tie
structure that decides same-priority order, the zero-request next-action
boundary supply, the model-forecast target supply, and whether a learned
re-ranking generalises across agent families.

No provider, no judge, no network, no dataset writes. Gold is read only to
*score* orderings; it never enters a packet (``detector.packet.build_packet``
rejects it). The numbers this prints are the ones quoted in
``docs/rsi-framework-design.md`` §8.

Usage:

    python scripts/probe_candidate_ordering.py
    python scripts/probe_candidate_ordering.py --out probe.json
    python scripts/probe_candidate_ordering.py --quiet
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys
from collections import Counter
from typing import Any, Iterable, Mapping, Sequence

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from detector.config import load_config  # noqa: E402
from detector.dataset import gold_fields, load_case, project_record  # noqa: E402
from detector.localization import gold_step_mapping  # noqa: E402
from detector.next_action import build_next_action_audit  # noqa: E402
from detector.packet import build_packet  # noqa: E402
from detector.step_uncertainty import (  # noqa: E402
    build_step_risk_profile,
    select_causal_candidates,
)

DEFAULT_ROOT = ROOT / "datasets" / "raw" / "agenthallu" / "AgentHallu"
SCHEMA = "evolution.candidate_ordering_probe.v1"
COVERAGE_KS = (1, 2, 3, 4, 5, 6, 8, 10)


def _policy() -> dict[str, Any]:
    return dict(load_config(require_credentials=False)["causal_audit"])


def _is_error(gold: Mapping[str, Any]) -> bool:
    return str(gold.get("is_hallucination")).strip().lower() in {"true", "yes", "1"}


def _family(case_id: str) -> str:
    return case_id.split("/", 1)[0]


def _verified_count(row: Mapping[str, Any]) -> int:
    return sum(
        1 for signal in row["risk_signals"]
        if signal["verification_status"] == "verified"
    )


def _current_key(row: Mapping[str, Any]) -> tuple:
    """The ordering the detector actually applies (``step_uncertainty``)."""
    return (row["priority"], -_verified_count(row), row["position"])


def _signal_bearing(profile: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        row for row in profile["by_event"]
        if row["score_status"] in {"scored", "truncated"} and row["risk_signals"]
    ]


def _collect(root: pathlib.Path, policy: Mapping[str, Any]) -> dict[str, Any]:
    """One pass over the corpus; every later measurement reads this."""
    events = 0
    signal_counts: Counter[str] = Counter()
    action_states: Counter[str] = Counter()
    action_patterns: Counter[str] = Counter()
    reachable_states: Counter[str] = Counter()
    reachable_patterns: Counter[str] = Counter()
    boundary_trajectories = 0
    boundaries = 0
    reachable_boundaries = 0
    reachable_trajectories = 0
    forecast_targets_total = 0
    forecast_trajectories = 0
    cases: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []

    for path in sorted(root.rglob("*.json")):
        case_id = path.relative_to(root).as_posix()
        try:
            record, gold = load_case(root, case_id)
            projected = project_record(record)
            packet = build_packet(projected)
        except Exception as exc:  # pragma: no cover - corpus shape guard
            failed.append({"case": case_id, "error": f"{type(exc).__name__}: {exc}"})
            continue
        profile = build_step_risk_profile(packet, policy=policy)
        selection = select_causal_candidates(
            profile, mandatory_event_ids=[], policy=policy,
        )
        events += len(packet["events"])
        for row in profile["by_event"]:
            for signal in row["risk_signals"]:
                signal_counts[signal["signal"]] += 1

        # Zero-request boundary supply. Two readings, because they answer
        # different questions: the *reachable* supply walks every recorded
        # event (what the deterministic auditor could check if it were asked),
        # while the *routed* supply is bounded by top_k (what the runtime
        # actually hands to it).
        audit = build_next_action_audit(packet, selection["top_k_event_ids"])
        boundaries += len(audit["comparisons"])
        if audit["comparisons"]:
            boundary_trajectories += 1
        for comparison in audit["comparisons"]:
            action_states[comparison["action_state"]] += 1
            action_patterns[comparison["divergence_pattern"]] += 1
        reachable = build_next_action_audit(
            packet, [event["event_id"] for event in packet["events"]],
        )
        reachable_boundaries += len(reachable["comparisons"])
        reachable_states.update(c["action_state"] for c in reachable["comparisons"])
        reachable_patterns.update(c["divergence_pattern"] for c in reachable["comparisons"])
        if reachable["comparisons"]:
            reachable_trajectories += 1

        # Model-forecast target supply (the paid channel).
        from detector.action_forecast import forecast_targets

        sources = {
            c["boundary"]["source_event_id"] for c in audit["comparisons"]
        }
        targets = {
            c["next_event_id"] for c in audit["comparisons"] if c["next_event_id"]
        }
        forecast = forecast_targets(packet, profile, sources, targets)
        forecast_targets_total += len(forecast)
        if forecast:
            forecast_trajectories += 1
        position_of = {event["event_id"]: event["position"] for event in packet["events"]}
        forecast_positions = {
            position_of[entry[2]] for entry in forecast if entry[2] in position_of
        }

        rows = _signal_bearing(profile)
        cases.append({
            "case": case_id,
            "family": _family(case_id),
            "error": _is_error(gold),
            "gold_position": (
                gold_step_mapping(projected, gold_fields(gold).get("hallucination_step"))["position"]
            ),
            "trajectory_length": len(packet["events"]),
            "rows": rows,
            "top_k_positions": [position_of[e] for e in selection["top_k_event_ids"]],
            "selection": {
                "state": selection["state"],
                "omitted": len(selection["omitted_event_ids"]),
                "unresolved": len(selection["unresolved_event_ids"]),
            },
            "forecast_positions": sorted(forecast_positions),
        })

    return {
        "events": events,
        "signal_counts": signal_counts,
        "action_states": action_states,
        "action_patterns": action_patterns,
        "boundaries": boundaries,
        "boundary_trajectories": boundary_trajectories,
        "reachable_boundaries": reachable_boundaries,
        "reachable_trajectories": reachable_trajectories,
        "reachable_states": reachable_states,
        "reachable_patterns": reachable_patterns,
        "forecast_targets_total": forecast_targets_total,
        "forecast_trajectories": forecast_trajectories,
        "cases": cases,
        "failed": failed,
    }


def _coverage(rows: Sequence[Mapping[str, Any]], key, ks: Iterable[int]) -> dict[str, Any]:
    counts = {k: 0 for k in ks}
    skipped = 0
    for case in rows:
        gold = case["gold_position"]
        if gold is None:
            skipped += 1
            continue
        ranked = sorted(case["rows"], key=key)
        positions = [row["position"] for row in ranked]
        for k in ks:
            counts[k] += int(gold in positions[:k])
    total = len(rows) - skipped
    return {
        "cases": total,
        "skipped": skipped,
        "counts": {str(k): counts[k] for k in ks},
        "rates": {
            str(k): (counts[k] / total if total else None) for k in ks
        },
    }


def _baselines(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Orderings that use no learned parameter, so a gain over them is real.

    ``reverse_within_priority`` matters more than it looks: it is the same
    priority table with the position tie-break flipped, and on this corpus it
    is close to the current ordering. A method that cannot beat it has not
    learned anything about the priority scale.
    """
    return {
        "current": _coverage(rows, _current_key, (1, 3)),
        "earliest": _coverage(rows, lambda row: (row["position"],), (1, 3)),
        "latest": _coverage(rows, lambda row: (-row["position"],), (1, 3)),
        "reverse_within_priority": _coverage(
            rows,
            lambda row: (row["priority"], -_verified_count(row), -row["position"]),
            (1, 3),
        ),
    }


def _tie_structure(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    more_than = Counter()
    duplicate_keys = 0
    positional_top3 = 0
    for case in rows:
        ranked = sorted(case["rows"], key=_current_key)
        keys = [(row["priority"], -_verified_count(row)) for row in case["rows"]]
        if len(set(keys)) < len(keys):
            duplicate_keys += 1
        best = min(row["priority"] for row in case["rows"])
        tied = sum(1 for row in case["rows"] if row["priority"] == best)
        for threshold in (1, 2, 3):
            more_than[threshold] += int(tied > threshold)
        by_position = sorted(case["rows"], key=lambda row: row["position"])
        if (
            [row["position"] for row in by_position[:3]]
            == [row["position"] for row in ranked[:3]]
        ):
            positional_top3 += 1
    return {
        "cases": total,
        "tied_above": {str(k): more_than[k] for k in (1, 2, 3)},
        "tied_above_rate": {
            str(k): more_than[k] / total if total else None for k in (1, 2, 3)
        },
        "duplicate_sort_keys": duplicate_keys,
        "duplicate_sort_key_rate": duplicate_keys / total if total else None,
        "position_order_equals_current_top3": positional_top3,
        "position_order_equals_current_top3_rate": positional_top3 / total if total else None,
        "distinct_signal_patterns": len({
            tuple(sorted({
                tuple(sorted({s["signal"] for s in row["risk_signals"]}))
                for row in case["rows"]
            }))
            for case in rows
        }),
    }


def _lift(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """P(event is the gold position | signal) against the event-level base rate.

    The base rate must be event-level (labelled cases divided by signal-bearing
    rows), not case-level: a signal's precision is a property of events, and
    dividing by the number of cases inflates every lift by the mean row count.
    """
    at_gold = Counter()
    elsewhere = Counter()
    labelled = 0
    observations = 0
    for case in rows:
        if case["gold_position"] is None:
            continue
        labelled += 1
        for row in case["rows"]:
            observations += 1
            signals = {s["signal"] for s in row["risk_signals"]}
            (at_gold if row["position"] == case["gold_position"] else elsewhere).update(signals)
    base_rate = labelled / observations if observations else None
    return {
        "labelled_cases": labelled,
        "signal_bearing_rows": observations,
        "base_rate": base_rate,
        "signals": {
            name: {
                "at_gold": at_gold[name],
                "elsewhere": elsewhere[name],
                "precision": (
                    at_gold[name] / (at_gold[name] + elsewhere[name])
                    if at_gold[name] + elsewhere[name] else None
                ),
                "lift": (
                    (at_gold[name] / (at_gold[name] + elsewhere[name])) / base_rate
                    if base_rate and at_gold[name] + elsewhere[name] else None
                ),
            }
            for name in sorted(set(at_gold) | set(elsewhere))
        },
    }


def _shrink(lift: Mapping[str, float], base: float, signal: str) -> float:
    """Shrink an estimated lift toward 1; unseen signals carry no information."""
    return lift.get(signal, 1.0)


def _fit_lift(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    at_gold = Counter()
    elsewhere = Counter()
    labelled = 0
    observations = 0
    for case in rows:
        if case["gold_position"] is None:
            continue
        labelled += 1
        for row in case["rows"]:
            signals = {s["signal"] for s in row["risk_signals"]}
            observations += 1
            (at_gold if row["position"] == case["gold_position"] else elsewhere).update(signals)
    base = labelled / observations if observations else 0.0
    table: dict[str, float] = {}
    for signal in set(at_gold) | set(elsewhere):
        hit, miss = at_gold[signal], elsewhere[signal]
        precision = (hit + 2 * base) / (hit + miss + 2)
        table[signal] = precision / base if base else 1.0
    return table


def _fit_position_prior(rows: Sequence[Mapping[str, Any]]) -> dict[int, float]:
    hits = Counter()
    occurrences = Counter()
    for case in rows:
        if case["gold_position"] is None:
            continue
        length = max(1, case["trajectory_length"])
        hits[min(9, int(10 * case["gold_position"] / length))] += 1
        for row in case["rows"]:
            occurrences[min(9, int(10 * row["position"] / length))] += 1
    return {
        bucket: (hits[bucket] + 1) / (occurrences[bucket] + 10)
        for bucket in range(10)
    }


def _leave_one_family_out(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    families = sorted({case["family"] for case in rows})
    learned = {"top1": 0, "top3": 0, "n": 0}
    prior = {"top1": 0, "top3": 0, "n": 0}
    static = {"top1": 0, "top3": 0, "n": 0}
    per_family: dict[str, Any] = {}
    for family in families:
        train = [c for c in rows if c["family"] != family]
        test = [c for c in rows if c["family"] == family]
        if not test or not train:
            continue
        table = _fit_lift(train)
        prior_table = _fit_position_prior(train)

        def lift_key(row, _table=table):
            signals = {s["signal"] for s in row["risk_signals"]}
            best = max((_shrink(_table, 1.0, s) for s in signals), default=1.0)
            return (-round(best, 2), row["position"])

        bucket = {"lift": [0, 0, 0], "prior": [0, 0, 0], "static": [0, 0, 0]}
        for case in test:
            if case["gold_position"] is None:
                continue
            length = max(1, case["trajectory_length"])
            ordered = sorted(case["rows"], key=lambda row: (row["priority"], -_verified_count(row), row["position"]))
            ranked_lift = sorted(case["rows"], key=lift_key)
            ranked_prior = sorted(
                case["rows"],
                key=lambda row: (
                    row["priority"],
                    -round(prior_table[min(9, int(10 * row["position"] / length))], 4),
                    row["position"],
                ),
            )
            for name, ranked, acc in (
                ("lift", ranked_lift, bucket["lift"]),
                ("prior", ranked_prior, bucket["prior"]),
                ("static", ordered, bucket["static"]),
            ):
                positions = [row["position"] for row in ranked]
                acc[0] += int(positions[0] == case["gold_position"])
                acc[1] += int(case["gold_position"] in positions[:3])
                acc[2] += 1
        per_family[family] = {
            name: {
                "cases": acc[2],
                "top1": acc[0] / acc[2] if acc[2] else None,
                "top3": acc[1] / acc[2] if acc[2] else None,
            }
            for name, acc in bucket.items()
        }
        for name, acc in bucket.items():
            target = {"lift": learned, "prior": prior, "static": static}[name]
            target["top1"] += acc[0]
            target["top3"] += acc[1]
            target["n"] += acc[2]
    pooled = {
        name: {
            "cases": acc["n"],
            "top1": acc["top1"] / acc["n"] if acc["n"] else None,
            "top3": acc["top3"] / acc["n"] if acc["n"] else None,
        }
        for name, acc in (("learned_lift", learned), ("position_prior", prior), ("static", static))
    }
    return {"pooled": pooled, "per_family": per_family}


def probe(root: pathlib.Path, policy: Mapping[str, Any]) -> dict[str, Any]:
    collected = _collect(root, policy)
    errors = [case for case in collected["cases"] if case["error"]]
    with_rows = [case for case in errors if case["rows"]]
    forecast_hit = sum(
        1 for case in with_rows
        if case["gold_position"] is not None
        and case["gold_position"] in case["forecast_positions"]
    )
    return {
        "schema_version": SCHEMA,
        "corpus": {
            "root": root.as_posix(),
            "trajectories": len(collected["cases"]) + len(collected["failed"]),
            "built": len(collected["cases"]),
            "failed": collected["failed"],
            "events": collected["events"],
            "error_cases": len(errors),
            "error_cases_with_candidates": len(with_rows),
        },
        "policy": {
            "score_source": policy["score_source"],
            "top_k": policy["top_k"],
            "max_projection_events": policy["max_projection_events"],
            "max_dependency_depth": policy["max_dependency_depth"],
        },
        "signal_counts": dict(collected["signal_counts"].most_common()),
        "next_action": {
            "routed": {
                "boundaries": collected["boundaries"],
                "trajectories": collected["boundary_trajectories"],
                "action_states": dict(collected["action_states"]),
                "divergence_patterns": dict(collected["action_patterns"]),
            },
            "reachable": {
                "boundaries": collected["reachable_boundaries"],
                "trajectories": collected["reachable_trajectories"],
                "boundary_rate_of_events": (
                    collected["reachable_boundaries"] / collected["events"]
                    if collected["events"] else None
                ),
                "action_states": dict(collected["reachable_states"]),
                "divergence_patterns": dict(collected["reachable_patterns"]),
            },
        },
        "forecast": {
            "targets": collected["forecast_targets_total"],
            "trajectories_with_target": collected["forecast_trajectories"],
            "trajectory_rate": (
                collected["forecast_trajectories"] / len(collected["cases"])
                if collected["cases"] else None
            ),
            "gold_in_target": forecast_hit,
            "gold_in_target_rate": (
                forecast_hit / len(with_rows) if with_rows else None
            ),
        },
        "coverage": _coverage(with_rows, _current_key, COVERAGE_KS),
        "baselines": {
            name: value for name, value in _baselines(with_rows).items()
            if value is not None
        },
        "ties": _tie_structure(with_rows),
        "signal_lift": _lift(with_rows),
        "leave_one_family_out": _leave_one_family_out(with_rows),
        "selection": dict(Counter(
            case["selection"]["state"] for case in collected["cases"]
        )),
        "trajectory_length": {
            "mean": statistics.mean(c["trajectory_length"] for c in collected["cases"]),
            "median": statistics.median(c["trajectory_length"] for c in collected["cases"]),
            "max": max(c["trajectory_length"] for c in collected["cases"]),
        },
    }


def _percent(value: Any) -> str:
    return "n/a" if value is None else f"{100 * value:.1f}%"


def summarize(report: Mapping[str, Any]) -> str:
    lines = [f"corpus: {report['corpus']['built']} built, {report['corpus']['events']} events"]
    cover = report["coverage"]
    lines.append(
        "top-k gold coverage: " + "  ".join(
            f"k={k} {_percent(cover['rates'][k])}" for k in map(str, COVERAGE_KS)
        )
    )
    base = report["baselines"]
    lines.append(
        "baselines (top-1 / top-3): current {} / {} | earliest {} / {} | latest {} / {} | reverse {}".format(
            _percent(base["current"]["rates"]["1"]), _percent(base["current"]["rates"]["3"]),
            _percent(base["earliest"]["rates"]["1"]), _percent(base["earliest"]["rates"]["3"]),
            _percent(base["latest"]["rates"]["1"]), _percent(base["latest"]["rates"]["3"]),
            _percent(base["reverse_within_priority"]["rates"]["1"]) + " / "
            + _percent(base["reverse_within_priority"]["rates"]["3"]),
        )
    )
    ties = report["ties"]
    lines.append(
        "ties: >1 at best priority {} | >3 {} | duplicate keys {} | distinct patterns {}".format(
            _percent(ties["tied_above_rate"]["1"]), _percent(ties["tied_above_rate"]["3"]),
            _percent(ties["duplicate_sort_key_rate"]), ties["distinct_signal_patterns"],
        )
    )
    action = report["next_action"]
    lines.append(
        "next action reachable: {} boundaries over {} trajectories ({} of events) states={}".format(
            action["reachable"]["boundaries"], action["reachable"]["trajectories"],
            _percent(action["reachable"]["boundary_rate_of_events"]),
            action["reachable"]["action_states"],
        )
    )
    lines.append(
        "next action routed via top_k: {} boundaries over {} trajectories states={}".format(
            action["routed"]["boundaries"], action["routed"]["trajectories"],
            action["routed"]["action_states"],
        )
    )
    forecast = report["forecast"]
    lines.append(
        "forecast: {} targets over {} trajectories ({}), gold in target {}".format(
            forecast["targets"], forecast["trajectories_with_target"],
            _percent(forecast["trajectory_rate"]), _percent(forecast["gold_in_target_rate"]),
        )
    )
    pooled = report["leave_one_family_out"]["pooled"]
    for name in ("static", "learned_lift", "position_prior"):
        entry = pooled[name]
        lines.append(
            "lopo {}: top-1 {} top-3 {} (n={})".format(
                name, _percent(entry["top1"]), _percent(entry["top3"]), entry["cases"],
            )
        )
    lift = report["signal_lift"]
    for name, entry in lift["signals"].items():
        lines.append(
            "  lift {:<24} at_gold={:<5} elsewhere={:<5} lift={}".format(
                name, entry["at_gold"], entry["elsewhere"],
                "n/a" if entry["lift"] is None else f"{entry['lift']:.2f}x",
            )
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--out", help="write the full probe as JSON")
    parser.add_argument("--quiet", action="store_true", help="suppress the summary")
    args = parser.parse_args()

    root = pathlib.Path(args.root)
    if not root.is_dir():
        raise SystemExit(f"corpus not found: {root}")

    report = probe(root, _policy())
    if args.out:
        path = pathlib.Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(f"wrote {path}")
    if not args.quiet:
        print(summarize(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
