"""Build and audit the provider-free HVSG v19.1 split and graph preflight."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, deque
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GRAPH = ROOT / "configs" / "agenthallu_hierarchical_verifier_skill_graph_v19_1.json"
DEFAULT_LEGACY_SPLIT = (
    ROOT
    / "artifacts"
    / "remote_archive_20260716"
    / "e3_1_binary_balanced_selective_skill_v16_1_1"
    / "pilot_split_freeze.json"
)

FORBIDDEN_PROVIDER_FIELDS = {
    "true_answer",
    "reference_answer",
    "agent_answer",
    "gold_label",
    "gold_responsible_step",
    "gold_explanation",
    "source",
    "case_id",
    "agent_id",
    "model_id",
    "outer_feedback",
}


def canonical_hash(value: Any) -> str:
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    raise ValueError(f"unsupported binary label value: {value!r}")


def discover_records(dataset_root: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for path in sorted(Path(dataset_root).glob("*/*.json")):
        case_id = f"{path.parent.name}/{path.stem}"
        if case_id in records:
            raise ValueError(f"duplicate case id: {case_id}")
        value = read_json(path)
        records[case_id] = value
    return records


def validate_graph_spec(graph: dict[str, Any]) -> dict[str, Any]:
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    errors: list[str] = []
    if not isinstance(nodes, list) or not nodes:
        errors.append("nodes_missing")
        nodes = []
    if not isinstance(edges, list):
        errors.append("edges_missing")
        edges = []
    node_ids = [node.get("id") for node in nodes if isinstance(node, dict)]
    if len(node_ids) != len(nodes) or any(not isinstance(node_id, str) or not node_id for node_id in node_ids):
        errors.append("node_id_invalid")
    if len(set(node_ids)) != len(node_ids):
        errors.append("node_id_duplicate")
    node_id_set = set(node_ids)
    adjacency = {node_id: set() for node_id in node_id_set}
    indegree = {node_id: 0 for node_id in node_id_set}
    invalid_edge_count = 0
    for edge in edges:
        if not isinstance(edge, dict) or edge.get("from") not in node_id_set or edge.get("to") not in node_id_set:
            invalid_edge_count += 1
            continue
        source, target = edge["from"], edge["to"]
        if target not in adjacency[source]:
            adjacency[source].add(target)
            indegree[target] += 1
    if invalid_edge_count:
        errors.append("edge_endpoint_invalid")
    queue = deque(sorted(node_id for node_id, degree in indegree.items() if degree == 0))
    visited = 0
    while queue:
        node_id = queue.popleft()
        visited += 1
        for target in sorted(adjacency[node_id]):
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if visited != len(node_id_set):
        errors.append("graph_cycle")
    mutable_nodes = [node for node in nodes if isinstance(node, dict) and node.get("mutable_activation") is True]
    if any(node.get("tier") != "atomic_verifier" for node in mutable_nodes):
        errors.append("mutable_non_leaf_node")
    evolution = graph.get("evolution") if isinstance(graph.get("evolution"), dict) else {}
    cap = evolution.get("maximum_activation_edits_per_fold")
    if not isinstance(cap, int) or cap < 0:
        errors.append("activation_edit_cap_invalid")
    required_audits = set(graph.get("required_audits") or [])
    required = {"graph_schema_validity", "DAG_acyclicity", "activation_edit_bound", "identifier_firewall"}
    if not required <= required_audits:
        errors.append("required_audits_incomplete")
    return {
        "decision": "pass" if not errors else "fail",
        "errors": errors,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "invalid_edge_count": invalid_edge_count,
        "dag_acyclic": visited == len(node_id_set),
        "mutable_atomic_verifier_count": len(mutable_nodes),
        "maximum_activation_edits_per_fold": cap,
        "graph_hash": canonical_hash(graph),
    }


def compile_fold_configuration(graph: dict[str, Any], active_leaf_ids: list[str]) -> dict[str, Any]:
    """Compile a catalog-bounded fold configuration without provider access."""
    validation = validate_graph_spec(graph)
    if validation["decision"] != "pass":
        raise ValueError("cannot compile an invalid graph")
    catalog_order = [
        node["id"] for node in graph["nodes"] if node.get("mutable_activation") is True
    ]
    selected = list(active_leaf_ids)
    if len(selected) != len(set(selected)):
        raise ValueError("duplicate leaf activation")
    if any(node_id not in catalog_order for node_id in selected):
        raise ValueError("activation outside mutable verifier catalog")
    if len(selected) > validation["maximum_activation_edits_per_fold"]:
        raise ValueError("activation edit bound exceeded")
    compiled = {
        "schema_version": "agenthallu.hvsg_v19_1.fold_configuration.v1",
        "graph_sha256": validation["graph_hash"],
        "activation_catalog_order": catalog_order,
        "active_leaf_ids": [node_id for node_id in catalog_order if node_id in selected],
        "activation_edit_count": len(selected),
        "maximum_activation_edits_per_fold": validation["maximum_activation_edits_per_fold"],
        "provider_calls_made": 0,
    }
    compiled["configuration_sha256"] = canonical_hash(compiled)
    return compiled


def legacy_roles(legacy_split: dict[str, Any]) -> tuple[set[str], set[str]]:
    checkpoint = legacy_split.get("checkpoint")
    fold = legacy_split.get("fold")
    if not isinstance(checkpoint, dict):
        raise ValueError("legacy split lacks checkpoint")
    if not isinstance(fold, dict):
        raise ValueError("legacy split lacks frozen outer fold")
    p0 = set(checkpoint.get("evolution_case_ids") or []) | set(checkpoint.get("selection_case_ids") or [])
    outer = set(fold.get("outer_case_ids") or [])
    if len(p0) != 64:
        raise ValueError(f"legacy P0 must contain 64 unique IDs, got {len(p0)}")
    if len(outer) != 106:
        raise ValueError(f"legacy outer must contain 106 unique IDs, got {len(outer)}")
    if p0 & outer:
        raise ValueError("legacy P0 overlaps sealed outer")
    return p0, outer


def role_rows(case_ids: set[str], records: dict[str, dict[str, Any]], role: str) -> list[dict[str, Any]]:
    rows = []
    for case_id in sorted(case_ids):
        record = records[case_id]
        rows.append({
            "case_id": case_id,
            "content_sha256": canonical_hash(record),
            "source": case_id.split("/", 1)[0],
            "gold_label": as_bool(record.get("is_hallucination")),
            "category": record.get("hallucination_category") if as_bool(record.get("is_hallucination")) else "None",
            "subcategory": record.get("hallucination_subcategory") if as_bool(record.get("is_hallucination")) else "None",
            "role": role,
        })
    return rows


def build_manifest(records: dict[str, dict[str, Any]], legacy_split: dict[str, Any]) -> dict[str, Any]:
    p0_ids, outer_ids = legacy_roles(legacy_split)
    missing = sorted((p0_ids | outer_ids) - set(records))
    if missing:
        raise ValueError(f"registered split references missing records: {len(missing)}")
    development_ids = set(records) - p0_ids - outer_ids
    if len(records) != 693:
        raise ValueError(f"AgentHallu must contain 693 records, got {len(records)}")
    if len(development_ids) != 523:
        raise ValueError(f"development split must contain 523 records, got {len(development_ids)}")
    p0_rows = role_rows(p0_ids, records, "P0_opened_diagnostic")
    outer_rows = role_rows(outer_ids, records, "O_sealed_outer")
    development_rows = role_rows(development_ids, records, "D_crossfit_development")
    p0_label_counts = Counter(row["gold_label"] for row in p0_rows)
    if p0_label_counts != Counter({True: 32, False: 32}):
        raise ValueError(f"P0 labels must be 32/32, got {dict(p0_label_counts)}")
    all_rows = p0_rows + development_rows + outer_rows
    return {
        "schema_version": "agenthallu.hvsg_v19_1.split_manifest.v1",
        "dataset": "AgentHallu",
        "case_count": len(records),
        "roles": {
            "P0_opened_diagnostic": p0_rows,
            "D_crossfit_development": development_rows,
            "O_sealed_outer": outer_rows,
        },
        "counts": {"P0": len(p0_rows), "D": len(development_rows), "O": len(outer_rows)},
        "p0_label_counts": {"clean": p0_label_counts[False], "hallucination": p0_label_counts[True]},
        "all_case_ids_sha256": canonical_hash(sorted(records)),
        "role_assignment_sha256": canonical_hash(all_rows),
        "outer_opened": False,
        "outer_feedback_used": False,
    }


def provider_projection(record: dict[str, Any]) -> dict[str, Any]:
    """The only data shape allowed to reach a future v19 detector."""
    return {"question": record.get("question"), "history": record.get("history")}


def audit_provider_firewall(records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    forbidden_hits = 0
    projection_hashes = []
    for record in records.values():
        projected = provider_projection(record)
        forbidden_hits += len(FORBIDDEN_PROVIDER_FIELDS & set(projected))
        projection_hashes.append(canonical_hash(projected))
    return {
        "decision": "pass" if forbidden_hits == 0 else "fail",
        "provider_packet_count": len(projection_hashes),
        "provider_packet_fields": ["question", "history"],
        "explicit_forbidden_field_hits": forbidden_hits,
        "provider_packet_projection_sha256": canonical_hash(sorted(projection_hashes)),
        "provider_calls_made": 0,
    }


def run_preflight(dataset_root: Path, graph_path: Path, legacy_split_path: Path, output_dir: Path) -> dict[str, Any]:
    graph = read_json(graph_path)
    legacy_split = read_json(legacy_split_path)
    records = discover_records(dataset_root)
    graph_report = validate_graph_spec(graph)
    seed_configuration = compile_fold_configuration(graph, []) if graph_report["decision"] == "pass" else None
    manifest = build_manifest(records, legacy_split)
    firewall_report = audit_provider_firewall(records)
    decision = "pass" if graph_report["decision"] == "pass" and firewall_report["decision"] == "pass" else "fail"
    report = {
        "schema_version": "agenthallu.hvsg_v19_1.preflight_report.v1",
        "experiment_id": "E3.1-HVSG-CrossFit-v19.1.0",
        "decision": decision,
        "graph": graph_report,
        "seed_configuration_sha256": seed_configuration["configuration_sha256"] if seed_configuration else None,
        "split_counts": manifest["counts"],
        "role_assignment_sha256": manifest["role_assignment_sha256"],
        "firewall": firewall_report,
        "provider_calls_made": 0,
        "outer_opened": False,
    }
    write_json(output_dir / "v19_1_split_manifest.json", manifest)
    write_json(output_dir / "v19_1_preflight_report.json", report)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--graph", type=Path, default=DEFAULT_GRAPH)
    parser.add_argument("--legacy-split", type=Path, default=DEFAULT_LEGACY_SPLIT)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = run_preflight(
        dataset_root=args.dataset_root,
        graph_path=args.graph,
        legacy_split_path=args.legacy_split,
        output_dir=args.output_dir,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["decision"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
