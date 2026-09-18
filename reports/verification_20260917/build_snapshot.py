"""Rebuild the Lisette snapshot with a recorded conflict resolution."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from detector.evidence import build_snapshot, render_evidence_block, snapshot_digest

out = Path(__file__).resolve().parent / "evidence_snapshot.json"

raw = (Path(__file__).resolve().parent / "evidence_sources" / "pg54338.txt").read_text(
    encoding="utf-8", errors="replace",
)
i = raw.find("116-ton topsail schooner")
passage = " ".join(raw[i - 120:i + 430].split())
excerpt = passage[passage.find("Even a private yacht"):]
excerpt = excerpt[:excerpt.find("paid off in the")] + "paid off in the following spring."
excerpt = excerpt.replace("_Lisette_", "Lisette")

excerpt2 = (
    "The 76 ton schooner 'Lisette' under sail. Creator: Kirk & Sons of Cowes. "
    "'Lisette' was built at Cowes in 1873."
)

snapshot = build_snapshot(
    query="Lisette tonnage 1873 topsail schooner Duke of Sutherland decoy 1917",
    derivation="task+claim",
    task_excerpt=(
        'How many tons was the topsail schooner "Lisette," built in 1873, '
        "which the Duke of Sutherland had formerly owned before she began "
        "working as a decoy craft in 1917?"
    ),
    claim=(
        "The trajectory's search result reported the tonnage of the Lisette as "
        "76 tons and the final answer repeated 76 tons."
    ),
    sources=[
        {
            "url": "https://www.gutenberg.org/cache/epub/54338/pg54338.txt",
            "title": ('Q-Ships and Their Story (E. Keble Chatterton, 1922), '
                      'ch. VII "More Sailing Ships"'),
            "publisher": "gutenberg.org",
            "retrieved_at": "2026-09-18T15:20:00+08:00",
            "excerpt": excerpt,
            "verifies": {"entity": "Lisette", "attribute": "tonnage",
                         "value": "116 tons"},
            "applicable_context": (
                "Describes the private yacht taken up in June 1916 and the "
                "vessel the task names: 116-ton topsail schooner Lisette, "
                "formerly the Duke of Sutherland's, built 1873, taken from "
                "Cowes to Falmouth, decoy service through April 1917."
            ),
        },
        {
            "url": ("https://www.heritage-images.com/preview/the-76-ton-"
                    "schooner-lisette-under-sail-creator-kirk-sons-of-cowes-"
                    "2696189"),
            "title": ("The 76 ton schooner 'Lisette' under sail. "
                      "Creator: Kirk & Sons of Cowes."),
            "publisher": "heritage-images.com",
            "retrieved_at": "2026-09-18T15:22:00+08:00",
            "excerpt": excerpt2,
            "verifies": {"entity": "Lisette", "attribute": "tonnage",
                         "value": "76 tons"},
            "applicable_context": (
                "A photograph catalogue caption. It states 76 tons and Cowes "
                "1873, but names neither the Duke of Sutherland nor decoy "
                "service, and gives no tonnage measure or register source."
            ),
        },
    ],
    resolutions=[{
        "entity": "Lisette",
        "attribute": "tonnage",
        "resolved_by": "E1",
        "reason": (
            "Applicability is decided by the task's own descriptors, not by "
            "repetition. The task names a vessel identified by four "
            "properties: topsail schooner, built 1873, formerly the Duke of "
            "Sutherland's, decoy craft from 1917. E1 states all four and "
            "therefore concerns the task's vessel; its 116 tons is the value "
            "the task asks for. E2 names the vessel and the build year only, "
            "identifies the photograph's creator, and cites no tonnage "
            "measure or register, so it cannot establish what the task asks."
        ),
    }],
)

out.write_text(
    json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
)
print("DIGEST:", snapshot_digest(snapshot))
print()
print(render_evidence_block(snapshot))
