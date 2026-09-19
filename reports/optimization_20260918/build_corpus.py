"""Rebuild the development corpus from archived primary-source records.

No benchmark answers, labels, aliases or synthetic answer sentences enter.
PDF pages remain archived verbatim; sentence boundaries only aid retrieval.
"""
import hashlib
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parent
OLD = ROOT.parent / "verification_20260917" / "corpus"


def main():
    dest = ROOT / "corpus"
    dest.mkdir(exist_ok=True)
    shutil.copytree(OLD / "documents", dest / "documents", dirs_exist_ok=True)
    manifest = json.loads((OLD / "corpus.json").read_text(encoding="utf-8"))
    fetched = {r["file"]: r for r in json.loads((ROOT / "sources/fetch.json").read_text())}
    specs = [
        ("unesco-water", "unesco-water.pdf", "unesco-water.pdf.page111.txt",
         "The United Nations World Water Development Report 2022: Groundwater: Making the Invisible Visible",
         "UNESCO", "PDF physical page 111, printed page 93; whitespace joined and sentence boundaries separated"),
        ("predemics", "predemics.pdf", "predemics.pdf.page39.txt",
         "PREDEMICS Project Final Report M1-M66", "European Commission CORDIS",
         "PDF page 39; whitespace joined and sentence boundaries separated"),
    ]
    for doc_id, raw, extracted, title, publisher, derivation in specs:
        text = (ROOT / "sources" / extracted).read_text(encoding="utf-8")
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"(?<=[.!?])\s+(?=[A-Z])", "\n\n", text)
        (dest / "documents" / (doc_id + ".txt")).write_text(text + "\n", encoding="utf-8")
        manifest["documents"].append(dict(doc_id=doc_id, path=f"documents/{doc_id}.txt",
            url=fetched[raw]["url"], title=title, publisher=publisher,
            retrieved_at=fetched[raw]["retrieved_at"], source_kind="report",
            derivation=derivation + "; archived original SHA256=" + fetched[raw]["sha256"]))
    # Exact short transcription from the club's primary match-statistics page.
    # Its actual date is retained; the question's May 23 is NOT substituted.
    text = "Fouls\n\nBayern Munich: 16; Inter Milan: 13\n"
    (dest / "documents/bayern-final.txt").write_text(text, encoding="utf-8")
    manifest["documents"].append(dict(doc_id="bayern-final", path="documents/bayern-final.txt",
        url="https://fcbayern.com/en/matches/profis/champions-league/2009-2010/fc-bayern-muenchen-inter-mailand-22-05-2010/statistics",
        title="Statistics: FC Bayern vs. Inter Milan - Champions League 09/10, 22 May 2010",
        publisher="FC Bayern", retrieved_at="2026-09-18T09:55:00+00:00", source_kind="statistics",
        derivation="Short exact transcription from web retrieval; direct HTTP download returned 403; match date differs from the question"))
    raw = json.loads((ROOT / "sources/clinical-trial.json").read_text())
    def flatten(value, prefix=""):
        if isinstance(value, dict):
            return [line for k, v in value.items() for line in flatten(v, prefix + "/" + k)]
        if isinstance(value, list):
            return [line for i, v in enumerate(value) for line in flatten(v, prefix + "/" + str(i))]
        return [f"{prefix}: {value}"]
    (dest / "documents/clinical-trial.txt").write_text("\n".join(flatten(raw)), encoding="utf-8")
    manifest["documents"].append(dict(doc_id="clinical-trial", path="documents/clinical-trial.txt",
        url=fetched["clinical-trial.json"]["url"],
        title=raw["protocolSection"]["identificationModule"]["officialTitle"],
        publisher="ClinicalTrials.gov", retrieved_at=fetched["clinical-trial.json"]["retrieved_at"],
        source_kind="registry", derivation="Mechanical JSON flattening; Jan-May 2018 are posting dates, not study dates; exact entity matching remains required"))
    (dest / "corpus.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(dest.rglob("*")) if p.is_file()}
    (ROOT / "corpus_hashes.json").write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
