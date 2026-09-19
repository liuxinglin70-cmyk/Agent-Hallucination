"""Audit frozen S/G retrieval without model calls or answer-label queries."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from detector.evidence import validate_snapshot
from detector.packet import build_packet
from detector.retrieval import build_case_snapshot, load_corpus
from evolution.config import load_config, resolve_path
from evolution.splits import build_split, load_record


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    config = load_config(args.config)
    split = build_split(config=config, dataset_root=resolve_path(config["dataset_root"]), mini=True)
    corpus = load_corpus(args.corpus)
    rows = []
    for role, cases in (("S", split.sentinel), ("G", split.gain)):
        for case in cases:
            record = load_record(resolve_path(config["dataset_root"]), case)
            question = build_packet(record)["question"]
            snapshot, report = build_case_snapshot(corpus, question)
            row = dict(split=role, case=case, question=question, covered=snapshot is not None,
                       schema_valid=None, retrieval=report)
            if snapshot:
                validate_snapshot(snapshot)
                row["schema_valid"] = True
                row["snapshot"] = case.replace("/", "__")
                (out / row["snapshot"]).write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
            rows.append(row)
    payload = dict(config=args.config, seed=config["split"]["seed"], corpus=args.corpus,
                   corpus_sha256={d["doc_id"]: hashlib.sha256(d["text"].encode()).hexdigest() for d in corpus["documents"]},
                   rows=rows, covered=sum(r["covered"] for r in rows), total=len(rows))
    (out / "retrieval.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    table = ["# Frozen S/G evidence coverage", "", "A valid schema does not prove semantic applicability. Gaps remain explicit.", "",
             "| Split | Case | Snapshot/schema | Retrieval outcome |", "|---|---|---|---|"]
    table += [f"| {r['split']} | {r['case']} | {'yes / valid' if r['covered'] else 'no'} | {r['retrieval']['outcome']} |" for r in rows]
    (out / "README.md").write_text("\n".join(table) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(out), "covered": payload["covered"], "total": len(rows)}))


if __name__ == "__main__":
    main()
