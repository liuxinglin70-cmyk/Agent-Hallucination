"""P1-C2 runtime certificate-class binder: the perception stage of L2.

The binder assigns every surviving certificate its class from detector
facts ONLY — the candidate claim, the certificate verdict and rationale,
the cited evidence refs, a projection of the referenced packet events and
the defense outcome. Gold labels, failure types, capability gaps and
policy expected effects are mechanically excluded: the input builder is a
closed field allowlist and every input is re-scanned for forbidden keys
before it can reach the provider route.

``unclassified`` is always an available answer: an unsure binder falls
back to baseline adjudication (the policy does not fire) instead of
guessing — fail-closed perception, mirroring the detector's authority
design. A model answer outside the closed vocabulary is a hard error,
never a silent downgrade.

CLI (serial calls, incremental output, resume by re-running):

    python -m sehd_rise2.evolution.binder bind \
        --run runs/random100_20260815_142446 \
        --out datasets/policy_candidates/v1/runtime_bindings.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from sehd_rise2.detector.config import load_config
from sehd_rise2.detector.provider import ProviderError, provider_call
from sehd_rise2.evolution.policy import CERTIFICATE_CLASSES
from sehd_rise2.evolution.replay import surviving_ids

RUNTIME_BINDINGS_SCHEMA = "sehd2.certificate_bindings.runtime.v1"
UNCLASSIFIED = "unclassified"
EVENT_CONTENT_CHARS = 1200

# The closed input contract. Nothing outside this allowlist may reach the
# binder; forbidden tokens are scanned recursively over the built inputs.
BINDING_INPUT_FIELDS = (
    "candidate_id",
    "candidate_claim",
    "certificate_verdict",
    "certificate_rationale",
    "evidence_refs",
    "defense_outcome",
    "packet_events",
)
FORBIDDEN_KEY_TOKENS = (
    "gold", "hallucination", "label", "failure", "prediction",
    "decision", "capability", "gap", "expected",
)

BINDER_SYSTEM = (
    "You are the certificate-class binder of a hallucination detector's "
    "policy layer. For every surviving verification certificate listed "
    "below, decide which conviction-basis class it belongs to, using "
    "ONLY the certificate, its candidate and the cited trajectory "
    "events.\n\n"
    "Classes (each definition is a boundary: CONVICTS WHEN is the test "
    "to run, NOT WHEN redirects to the confusable class):\n"
    + "\n".join(
        f"- {name}: {definition}"
        for name, definition in CERTIFICATE_CLASSES.items()
    )
    + "\n- unclassified: the evidence shown does not let you run the "
    "decisive test confidently; the policy layer then keeps baseline "
    "behaviour.\n\n"
    "Decision procedure, per certificate:\n"
    "1. Test every specific class BEFORE content_contradiction — it is "
    "the residual and binds only when every other class's test fails.\n"
    "2. Judge what the asserted deviation actually IS, not how the "
    "certificate words it: a certificate phrasing a wording-level, "
    "near-tie, presentational, absence-based or premise-level "
    "difference as a 'contradiction' or 'failure' still binds to the "
    "specific class whose test the underlying difference passes.\n"
    "3. Before binding content_contradiction on a deviation from a "
    "cited source, put the two sides side by side and run the "
    " neighbour tests on the pair: (a) if the opposing source is the "
    "task text or a user statement — a premise, not an observation — "
    "and the agent's content inherits it, bind premise_conflict; "
    "(b) if the pair differs only in wording, formatting, rounding or "
    "a near-tie margin inside the source's precision, bind "
    "expression_variance; (c) if the added material unpacks, renders "
    "or evaluatively qualifies an observed specific, bind "
    "presentational_addition; (d) if the source merely lacks the "
    "material while no cited slice contradicts it and it is grounded "
    "elsewhere, bind absence_based. content_contradiction needs a "
    "genuine contradiction: a source asserting what the agent's "
    "specific denies, or an agent-authored specific no source "
    "supports.\n"
    "4. Bind every certificate in this case; certificates of the same "
    "case may legitimately get different classes.\n"
    "5. Abstaining is a normal answer for this layer. Answer confidence "
    "high only when your quoted phrase settles the class test by "
    "itself; answer low when two classes stayed plausible or you had "
    "to weigh material not shown. Low confidence falls back to "
    "baseline behaviour — a guessed class suppresses or keeps a "
    "conviction incorrectly.\n"
    "Reply with ONLY a JSON object of the exact shape:\n"
    '{"bindings": [{"candidate_id": "c1", "class": "<class name or '
    'unclassified>", "confidence": "<high or low>", "evidence_basis": '
    '"<one sentence quoting the decisive phrase; for premise_conflict '
    'quote the premise sentence>"}]}'
)


def load_run_packets(run_dir: Path) -> dict[str, dict[str, Any]]:
    """case_id -> {result, packet} — the label-free run facts (no gold)."""
    tree: dict[str, dict[str, Any]] = {}
    for result_path in sorted((Path(run_dir) / "cases").glob(
        "*/result.json",
    )):
        result = json.loads(result_path.read_text(encoding="utf-8"))
        case_id = str(result.get("case_id") or "")
        if not case_id:
            continue
        packet_path = result_path.parent / "packet.json"
        tree[case_id] = {
            "result": result,
            "packet": (
                json.loads(packet_path.read_text(encoding="utf-8"))
                if packet_path.exists() else {"events": []}
            ),
        }
    return tree


def _event_projection(
    packet: Mapping[str, Any], wanted_ids: set[str],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for event in packet.get("events") or []:
        event_id = str(event.get("event_id") or "")
        if event_id not in wanted_ids:
            continue
        interactions = [
            {
                "tool_name": interaction.get("tool_name"),
                "arguments": json.dumps(
                    interaction.get("arguments"),
                    ensure_ascii=False,
                )[:400],
            }
            for interaction in event.get("tool_interactions") or []
        ]
        events.append({
            "event_id": event_id,
            "role": event.get("role"),
            "content": str(event.get("content") or "")[:EVENT_CONTENT_CHARS],
            "tool_interactions": interactions,
        })
    return events


def binding_inputs(
    run_tree: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """One label-free binding input per case: all its surviving
    certificates together, so co-convicting certificates are classified
    with full case context."""
    inputs: list[dict[str, Any]] = []
    for case_id in sorted(run_tree):
        result = run_tree[case_id]["result"]
        packet = run_tree[case_id]["packet"]
        proof = result.get("proof_adjudication") or {}
        candidates = {
            str(candidate.get("candidate_id")): candidate
            for candidate in proof.get("census") or []
        }
        certificates = {
            str(cert.get("candidate_id")): cert
            for cert in proof.get("certificates") or []
        }
        entries: list[dict[str, Any]] = []
        for candidate_id in surviving_ids(result):
            candidate = candidates.get(candidate_id, {})
            certificate = certificates.get(candidate_id, {})
            refs = [
                str(ref) for ref in candidate.get("evidence_refs") or []
            ]
            locus = str(candidate.get("locus_event_id") or "")
            entries.append({
                "candidate_id": candidate_id,
                "candidate_claim": str(candidate.get("claim") or ""),
                "certificate_verdict": str(
                    certificate.get("verdict") or ""
                ),
                "certificate_rationale": str(
                    certificate.get("rationale") or ""
                ),
                "evidence_refs": refs,
                "defense_outcome": "upheld",
                "packet_events": _event_projection(
                    packet, set(refs) | {locus},
                ),
            })
        if entries:
            inputs.append({"case_id": case_id, "certificates": entries})
    return inputs


def assert_label_free(inputs: list[Mapping[str, Any]]) -> None:
    """Mechanical gold-exclusion: allowlisted keys, no forbidden tokens.

    The allowlist binds at two levels — the case input (``case_id`` /
    ``certificates``) and each certificate entry (``BINDING_INPUT_FIELDS``);
    deeper structures such as packet-event projections are only checked
    against forbidden key tokens.
    """

    def _scan(value: Any, path: str, *, cert_level: bool) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                key_text = str(key).lower()
                if cert_level and str(key) not in BINDING_INPUT_FIELDS:
                    raise ValueError(
                        f"certificate field {key!r} outside the "
                        f"allowlist"
                    )
                if any(
                    token in key_text for token in FORBIDDEN_KEY_TOKENS
                ):
                    raise ValueError(f"forbidden key {key!r} at {path}")
                _scan(item, f"{path}.{key}", cert_level=False)
        elif isinstance(value, list):
            cert_children = path.endswith(".certificates")
            for item in value:
                _scan(item, path, cert_level=cert_children)

    for entry in inputs:
        for key, item in entry.items():
            if key not in ("case_id", "certificates"):
                raise ValueError(
                    f"binding input field {key!r} outside the allowlist"
                )
        _scan(entry, "", cert_level=False)
        text = json.dumps(entry, ensure_ascii=False).lower()
        for token in (
            "is_hallucination", "hallucination_step", "gold_label",
        ):
            if token in text:
                raise ValueError(
                    f"gold-bearing token {token!r} in binding input "
                    f"{entry.get('case_id')}"
                )


def normalize_binding(
    parsed: Any, *, case_id: str, expected_ids: list[str],
) -> list[dict[str, str]]:
    """Validate one batched model answer for a whole case.

    Every listed certificate must receive exactly one binding inside the
    closed vocabulary; anything else is a hard error, never a silent
    downgrade. A ``low`` confidence on a classified answer is demoted
    mechanically to ``unclassified`` — the fail-closed abstention gate,
    enforced here rather than trusted to the model's own hesitation.
    """
    if not isinstance(parsed, Mapping):
        raise ValueError(f"{case_id}: binder returned no JSON object")
    answers = parsed.get("bindings")
    if not isinstance(answers, list):
        raise ValueError(f"{case_id}: binder answer lacks a bindings list")
    records: list[dict[str, str]] = []
    for answer in answers:
        if not isinstance(answer, Mapping):
            raise ValueError(f"{case_id}: non-object binding entry")
        candidate_id = str(answer.get("candidate_id") or "")
        if candidate_id not in expected_ids:
            raise ValueError(
                f"{case_id}: binding for unknown certificate "
                f"{candidate_id!r}"
            )
        certificate_class = str(answer.get("class") or "")
        confidence = str(answer.get("confidence") or "")
        basis = str(answer.get("evidence_basis") or "")
        if certificate_class == UNCLASSIFIED:
            record_class, status = UNCLASSIFIED, UNCLASSIFIED
        elif certificate_class in CERTIFICATE_CLASSES:
            if confidence not in ("high", "low"):
                raise ValueError(
                    f"{case_id}/{candidate_id}: classified binding "
                    f"without high/low confidence"
                )
            if not basis:
                raise ValueError(
                    f"{case_id}/{candidate_id}: classified binding "
                    f"without basis"
                )
            if confidence == "low":
                record_class, status = UNCLASSIFIED, UNCLASSIFIED
                basis = f"low-confidence demotion from {certificate_class}: {basis}"
            else:
                record_class, status = certificate_class, "classified"
        else:
            raise ValueError(
                f"{case_id}/{candidate_id}: answer {certificate_class!r} "
                f"outside the closed vocabulary"
            )
        records.append({
            "case_id": case_id,
            "candidate_id": candidate_id,
            "certificate_class": record_class,
            "basis": basis or "binder abstained",
            "binding_status": status,
        })
    answered = {record["candidate_id"] for record in records}
    missing = [cid for cid in expected_ids if cid not in answered]
    if missing:
        raise ValueError(
            f"{case_id}: binder left certificates unanswered: {missing}"
        )
    return records


def bind_all(
    *, run_dir: Path, out_path: Path, limit: int | None = None,
) -> int:
    """Bind every case's surviving certificates, one serial call each."""
    run_tree = load_run_packets(run_dir)
    if not run_tree:
        print(f"no cases found under {run_dir}")
        return 1
    inputs = binding_inputs(run_tree)
    assert_label_free(inputs)
    total = sum(
        len(case_input["certificates"]) for case_input in inputs
    )
    if limit is not None:
        inputs = inputs[:limit]
    done: dict[tuple[str, str], dict[str, str]] = {}
    if out_path.exists():
        previous = json.loads(out_path.read_text(encoding="utf-8"))
        for entry in previous.get("bindings") or []:
            done[(entry["case_id"], entry["candidate_id"])] = dict(entry)
    config = load_config()
    route = dict(config["verification_route"])
    route["_route_name"] = "binder"

    def _persist() -> None:
        payload = {
            "schema_version": RUNTIME_BINDINGS_SCHEMA,
            "derived_from": {
                "run": str(run_dir),
                "class_definitions": (
                    "sehd_rise2/evolution/policy.py::CERTIFICATE_CLASSES"
                ),
            },
            "binding_source": (
                f"runtime binder route {route.get('model')} "
                "(verification route, serial per-case batches, "
                "label-free inputs)"
            ),
            "review_status": "machine_generated",
            "bindings": [done[key] for key in sorted(done)],
        }
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    failures = 0
    for case_input in inputs:
        case_id = case_input["case_id"]
        pending = [
            entry for entry in case_input["certificates"]
            if (case_id, entry["candidate_id"]) not in done
        ]
        if not pending:
            continue
        user = json.dumps(
            {"case_id": case_id, "certificates": pending},
            ensure_ascii=False, indent=1,
        )
        try:
            parsed, meta = provider_call(
                route=route, system=BINDER_SYSTEM, user=user,
                purpose="binder",
            )
            records = normalize_binding(
                parsed, case_id=case_id,
                expected_ids=[
                    entry["candidate_id"] for entry in pending
                ],
            )
        except (ProviderError, ValueError) as exc:
            print(f"FAIL {case_id}: {exc}")
            failures += 1
            continue
        for record in records:
            done[(record["case_id"], record["candidate_id"])] = record
        _persist()
        print(
            f"bound {case_id}: "
            + ", ".join(
                f"{r['candidate_id']}={r['certificate_class']}"
                for r in records
            )
        )
    remaining = total - len(done)
    print(
        f"bound {len(done)}/{total} certificates "
        f"({max(remaining, 0)} remaining, {failures} case failures)"
    )
    return 1 if remaining > 0 or failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    bind_parser = sub.add_parser(
        "bind", help="bind every surviving certificate (serial, resumable)",
    )
    bind_parser.add_argument("--run", type=Path, required=True)
    bind_parser.add_argument("--out", type=Path, required=True)
    bind_parser.add_argument(
        "--limit", type=int, default=None,
        help="bind only the first N certificates (smoke runs)",
    )
    args = parser.parse_args()
    return bind_all(
        run_dir=args.run, out_path=args.out, limit=args.limit,
    )


if __name__ == "__main__":
    raise SystemExit(main())
