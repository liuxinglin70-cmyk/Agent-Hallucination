"""Retrieve and freeze the development evidence corpus (label-free by design).

Every document is fetched by URL, saved with its bytes, and hashed. The
questions driving the fetches come from case *questions* only; no gold
label, reference answer or label reason is consulted anywhere in this file.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / 'corpus'
USER_AGENT = "AgentHallu-evidence-retrieval/0.1 (research; contact: local)"

DOCUMENTS = [
    {
        "doc_id": "q-ships-1922",
        "url": "https://www.gutenberg.org/cache/epub/54338/pg54338.txt",
        "title": "Q-Ships and Their Story (E. Keble Chatterton, 1922)",
        "publisher": "gutenberg.org",
        "retrieved_at": "2026-09-18T16:40:00+08:00",
        "source_kind": "book",
        "derivation": "primary account of the decoy-ship service; fetched for the Lisette tonnage question",
    },
    {
        "doc_id": "heritage-lisette",
        "url": ("https://www.heritage-images.com/preview/the-76-ton-schooner-"
                "lisette-under-sail-creator-kirk-sons-of-cowes-2696189"),
        "title": "The 76 ton schooner 'Lisette' under sail (Heritage Images)",
        "publisher": "heritage-images.com",
        "retrieved_at": "2026-09-18T16:42:00+08:00",
        "source_kind": "catalogue",
        "derivation": "photograph catalogue caption surfaced by the same search",
    },
    {
        "doc_id": "musicbrainz-jsr",
        "url": ("https://musicbrainz.org/ws/2/release/"
                "be5b825d-5bb7-38b2-8fd7-dc0eef9501a9"
                "?inc=recordings+artist-credits&fmt=json"),
        "title": ("MusicBrainz release be5b825d: Jet Set Radio Original "
                  "Sound Tracks (2000-12-20, JP)"),
        "publisher": "musicbrainz.org",
        "retrieved_at": "2026-09-18T16:45:00+08:00",
        "source_kind": "database",
        "derivation": "release tracklist for the 2000 soundtrack question",
    },
]

# A second, independent listing for the same release, to exercise the
# conflict/independence path with more than a single source family.
EXTRA = [
    {
        "doc_id": "discogs-jsr",
        "url": "https://www.discogs.com/release/2810209",
        "title": "Jet Set Radio Original Sound Tracks (2000, CD) — Discogs",
        "publisher": "discogs.com",
        "retrieved_at": "2026-09-18T16:47:00+08:00",
        "source_kind": "database",
        "derivation": "second listing of the same 2000 release",
    },
]


def fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    raw = urllib.request.urlopen(request, timeout=60).read()
    return raw.decode("utf-8", errors="replace")


def text_from_html(html: str) -> str:
    import re

    body = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    body = re.sub(r"(?i)<br\s*/?>", "\n", body)
    body = re.sub(r"(?i)</(p|div|li|tr|h[1-6])>", "\n\n", body)
    text = re.sub(r"<[^>]+>", " ", body)
    text = text.replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"')
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def json_as_text(payload: str) -> str:
    """Flatten a JSON API reply into readable lines, stable and auditable."""
    data = json.loads(payload)
    lines: list[str] = []

    def walk(node, path=""):
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}/{key}" if path else str(key))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")
        elif node not in (None, ""):
            lines.append(f"{path}: {node}")

    walk(data)
    return "\n".join(lines)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    docs_dir = OUT / "documents"
    docs_dir.mkdir(exist_ok=True)
    manifest = {"schema_version": "detector.corpus.v1", "documents": []}
    for spec in DOCUMENTS + EXTRA:
        target = docs_dir / f"{spec['doc_id']}.txt"
        if not target.exists():
            try:
                payload = fetch(spec["url"])
            except Exception as exc:
                print(f"SKIP {spec['doc_id']}: {type(exc).__name__}: {exc}")
                continue
            text = (
                json_as_text(payload)
                if spec["url"].endswith("fmt=json") or "fmt=json" in spec["url"]
                else text_from_html(payload)
            )
            target.write_text(text, encoding="utf-8")
            print(f"fetched {spec['doc_id']}: {len(text)} chars")
            time.sleep(1)
        else:
            print(f"cached  {spec['doc_id']}: {len(target.read_text(encoding='utf-8'))} chars")
        manifest["documents"].append({
            "doc_id": spec["doc_id"],
            "path": f"documents/{spec['doc_id']}.txt",
            "url": spec["url"],
            "title": spec["title"],
            "publisher": spec["publisher"],
            "retrieved_at": spec["retrieved_at"],
            "source_kind": spec["source_kind"],
            "derivation": spec["derivation"],
        })
    (OUT / "corpus.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"\ncorpus documents: {len(manifest['documents'])}")
    print("sha256 of each document:")
    for entry in manifest["documents"]:
        data = (OUT / entry["path"]).read_bytes()
        print(f"  {entry['doc_id']:22s} {hashlib.sha256(data).hexdigest()[:16]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
