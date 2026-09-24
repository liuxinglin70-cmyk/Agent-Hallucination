"""Which deterministic predates may authorize a terminal conviction, and why.

The detector has a zero-LLM path to a terminal ``verified_error``: a
deterministic predicate reports a convicting verdict, a ``decision_guard``
binds that evidence row, and ``detector.adjudication`` records an
``authoritative`` conviction without the judge, the recall net or the defense
hearing ever being consulted. Nothing in that path measures whether the
predicate is *right*: ``terminal_authority`` says which executor may convict,
not whether its verdicts are accurate, and a rule whose text-matching fires on
well-formed answers turns "the parser recognised a phrase" into "the agent
hallucinated".

This module is the missing measurement. A built-in deterministic predicate may
authorize a conviction only while a **calibration record** for it holds:

* the record was measured on the labelled corpus (gold-clean cases) and found
  **zero** false positives there -- a predicate that convicts a clean case has no
  business convicting anything;
* the record still describes the code that produced it: each predicate's
  *derived dependency fingerprint* is recomputed in-process and must match.
  The fingerprint is computed by walking the runner's actual code objects and
  the module-level data it reads, transitively -- not by hashing the whole file
  -- so editing a sibling predicate does not invalidate an unrelated record,
  while editing a helper the predicate calls does;
* the record still describes the data it was measured on: the corpus file
  digests must match, or the measurement no longer describes the clean set it
  claims to;
* the live re-measurement agrees with the recorded counts, so the record is
  evidence rather than a number a reader has to trust;
* the record's conviction vocabulary is the runtime's current one.

Any failure makes the predicate **uncalibrated**, and an uncalibrated predicate
is not conviction-authorized: :func:`detector.interventions.conviction_verdicts_for`
returns the empty set for it, so a guard that binds it is suppressed and the
case falls through to the ordinary judge/defense path instead of being
convicted. Refusal is the safe direction -- the case is then decided by
mechanisms that do not depend on this predicate.

Two deliberate neighbours:

* ``evolution.calibration`` audits the *judge's* stated confidence and is
  ``calibration-only``: it scores nothing and admits nothing. This module is the
  other half of that idea, at the other end of the authority spectrum: the
  mechanical executor may convict only with a measurement behind it, so here the
  measurement is an admission decision. The two are different subjects (an LLM's
  ordinal certainty vs. a deterministic parser's error rate on a labelled set)
  and neither can answer for the other.
* ``evolution.backends.source_digest`` fingerprints the whole source tree so a
  cache cannot outlive the code. That is the right shape for "any code change
  invalidates derived data"; it is the wrong shape here, because a predicate's
  record must survive edits that cannot change its verdicts and must not survive
  edits that can. Hence the derived dependency walk below.

Nothing here reads the dataset eagerly, calls a provider or writes anything:
``scripts/calibrate_conviction_predicates.py`` writes the record, and this
module only reads and verifies it.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
import types
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCHEMA = "detector.conviction_calibration.v1"
METRIC_ROLE = "conviction-authority-admission"

#: The record the runtime reads when no other one is supplied.
DEFAULT_ARTIFACT = (
    Path(__file__).resolve().parent / "calibration" / "conviction_predicates.json"
)

#: The labelled corpus a conviction calibration must be measured on. Only the
#: gold-clean half is load-bearing (the false-positive rate); the error half is
#: recorded so a reader can see what recall the predicate still has.
DEFAULT_CORPUS_ROOT = (
    Path(__file__).resolve().parent.parent / "datasets" / "raw" / "agenthallu"
    / "AgentHallu"
)

#: A record measured on fewer clean cases than this is not evidence that a
#: predicate has a zero false-positive rate -- "0 of 3" is what an untested
#: parser looks like. The corpus this project ships has 250.
MIN_CLEAN_CASES = 50

#: The corpus spells its gold label as a string.
_CLEAN_LABELS = frozenset({"false", "clean", "no", "0"})
_ERROR_LABELS = frozenset({"true", "yes", "1"})

_REASONS = {
    "artifact_unavailable": "no calibration record could be read",
    "artifact_malformed": "the calibration record is not a mapping",
    "schema_mismatch": "the calibration record has another schema version",
    "content_digest_mismatch": "the calibration record was edited after it was written",
    "predicate_not_recorded": "the record has no entry for this predicate",
    "conviction_verdicts_mismatch": (
        "the record was measured against a different conviction vocabulary"
    ),
    "clean_set_false_positive": (
        "the predicate convicted at least one gold-clean case when measured"
    ),
    "insufficient_clean_cases": (
        "the record was measured on too few gold-clean cases to support a "
        "zero-false-positive claim"
    ),
    "unbuildable_cases": "some corpus cases could not be compiled into packets",
    "corpus_unavailable": "the labelled corpus the record was measured on is not readable",
    "corpus_fingerprint_mismatch": "the corpus changed after the measurement",
    "clean_fingerprint_mismatch": "the gold-clean cases changed after the measurement",
    "source_fingerprint_mismatch": (
        "the predicate's code or the data it reads changed after the measurement"
    ),
    "remeasurement_disagreement": (
        "a live re-measurement disagrees with the counts in the record"
    ),
}


# ---------------------------------------------------------------------------
# Corpus identity
# ---------------------------------------------------------------------------

def _gold_label(value: Any) -> bool | None:
    """``True`` for a clean case, ``False`` for an error case, ``None`` unknown."""
    text = str(value).strip().lower()
    if text in _CLEAN_LABELS:
        return True
    if text in _ERROR_LABELS:
        return False
    return None


def iter_case_paths(corpus_root: str | Path) -> list[Path]:
    return sorted(Path(corpus_root).rglob("*.json"))


def corpus_fingerprint(corpus_root: str | Path) -> dict[str, Any]:
    """Content identity of the labelled corpus, clean subset included.

    Every file participates in ``fingerprint``: a corpus edit anywhere means the
    record no longer describes the data it was taken from. ``clean_fingerprint``
    covers the gold-clean subset alone, which is the part the admission
    argument actually rests on, so a reader can tell which of the two moved.

    A case whose label cannot be read is neither clean nor error; it makes
    ``unlabelled`` nonzero, which refuses admission rather than silently
    shrinking the clean set.
    """
    root = Path(corpus_root)
    digest = hashlib.sha256()
    clean_digest = hashlib.sha256()
    cases = clean = error = unlabelled = 0
    for path in iter_case_paths(root):
        relative = path.relative_to(root).as_posix()
        payload = path.read_bytes()
        digest.update(relative.encode("utf-8"))
        digest.update(payload)
        cases += 1
        label = _case_label(path)
        if label is True:
            clean += 1
            clean_digest.update(relative.encode("utf-8"))
            clean_digest.update(payload)
        elif label is False:
            error += 1
        else:
            unlabelled += 1
    return {
        "root": root.as_posix(),
        "cases": cases,
        "clean_cases": clean,
        "error_cases": error,
        "unlabelled_cases": unlabelled,
        "fingerprint": digest.hexdigest(),
        "clean_fingerprint": clean_digest.hexdigest(),
    }


def _case_label(path: Path) -> bool | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, Mapping):
        return None
    return _gold_label(raw.get("is_hallucination"))


# ---------------------------------------------------------------------------
# Source identity: the derived dependency fingerprint
# ---------------------------------------------------------------------------

#: Depth and width guards. The walk is over module-level names referenced by the
#: runner's code objects, which is a small graph; the caps exist so a future
#: predicate that reaches into a large structure fails loudly here instead of
#: taking a long walk at import time.
_MAX_DEPENDENCIES = 512

_CANONICAL_SEQUENCES = (tuple, list, set, frozenset)


def _canonical(value: Any) -> str:
    """A stable text form of one reachable value.

    Deliberately not ``repr``: an object address or a dict order would make the
    fingerprint unstable between processes, and an unstable fingerprint refuses
    admission for no reason -- which trains a reader to ignore it.
    """
    if isinstance(value, types.ModuleType):
        return f"module:{value.__name__}"
    if isinstance(value, (types.FunctionType, types.BuiltinFunctionType,
                          types.MethodType, types.BuiltinMethodType)):
        try:
            return inspect.getsource(value)
        except (OSError, TypeError):
            return f"callable:{getattr(value, '__module__', '?')}." \
                   f"{getattr(value, '__qualname__', getattr(value, '__name__', '?'))}"
    if isinstance(value, type):
        return f"class:{value.__module__}.{value.__qualname__}"
    if isinstance(value, re.Pattern):
        return f"pattern:{value.pattern}|{value.flags}"
    if isinstance(value, str):
        return f"str:{value}"
    if isinstance(value, bytes):
        return f"bytes:{value.hex()}"
    if value is None or isinstance(value, (bool, int, float)):
        return f"const:{value!r}"
    if isinstance(value, Mapping):
        items = sorted(
            (str(key), _canonical(item)) for key, item in value.items()
        )
        return "map:{" + ",".join(f"{key}={item}" for key, item in items) + "}"
    if isinstance(value, _CANONICAL_SEQUENCES):
        items = sorted(_canonical(item) for item in value)
        return "seq:[" + ",".join(items) + "]"
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        items = sorted(_canonical(item) for item in value)
        return "iter:[" + ",".join(items) + "]"
    return f"object:{type(value).__module__}.{type(value).__qualname__}"


def _namespaced(value: Any) -> str:
    if isinstance(value, types.ModuleType):
        return value.__name__
    if isinstance(value, (types.FunctionType, types.MethodType)):
        return f"{value.__module__}.{value.__qualname__}"
    if isinstance(value, type):
        return f"{value.__module__}.{value.__qualname__}"
    return type(value).__qualname__


def _references(value: Any) -> list[str]:
    """Module-level names one function reads, nested code objects included.

    A comprehension or lambda inside a predicate is its own code object stored
    in the enclosing function's constants; reading only the outer ``co_names``
    would miss a helper a comprehension calls, which is exactly the kind of
    dependency the fingerprint exists to catch.
    """
    code = getattr(value, "__code__", None)
    if code is None:
        return []
    return sorted(_code_names(code))


def _code_names(code: types.CodeType) -> set[str]:
    names = set(code.co_names)
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            names |= _code_names(const)
    return names


def _resolve(value: Any, name: str) -> Any:
    namespace = getattr(value, "__globals__", None)
    if not isinstance(namespace, Mapping):
        return None
    return namespace.get(name)


def dependency_items(predicate: str) -> dict[str, str]:
    """``{dotted name: digest of its canonical form}`` for one predicate.

    The walk starts at the runner function named in
    ``detector.predicates._RUNNERS`` and follows the module-level names each
    reachable function references, transitively. Every name is recorded and
    digested, so the fingerprint answers a question a whole-file digest cannot:
    *did anything this predicate reads change?*
    """
    import detector.predicates as predicate_module

    runner = predicate_module._RUNNERS.get(predicate)
    if runner is None:
        raise KeyError(f"predicate {predicate!r} has no deterministic executor")
    module = predicate_module.__name__
    items: dict[str, str] = {}
    pending: list[tuple[str, Any]] = [(f"{module}.{name}", _resolve(runner, name))
                                      for name in _references(runner)]
    pending = [(key, value) for key, value in pending if value is not None]
    pending.insert(0, (f"{module}.{runner.__name__}", runner))
    while pending:
        key, value = pending.pop()
        if key in items:
            continue
        canonical = _canonical(value)
        items[key] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if len(items) > _MAX_DEPENDENCIES:
            raise ValueError(
                f"dependency walk for {predicate!r} exceeded "
                f"{_MAX_DEPENDENCIES} entries; narrow the predicate's reachable "
                "module-level state or raise the cap deliberately"
            )
        owner = value if isinstance(
            value, (types.FunctionType, types.BuiltinFunctionType),
        ) else None
        if owner is None:
            continue
        for name in _references(owner):
            resolved = _resolve(owner, name)
            if resolved is None or resolved is value:
                continue
            pending.append((f"{_namespaced(resolved)}:{name}", resolved))
    return items


def source_fingerprint(predicate: str) -> str:
    """Digest over everything one predicate's verdicts depend on."""
    items = dependency_items(predicate)
    digest = hashlib.sha256()
    for key in sorted(items):
        digest.update(key.encode("utf-8"))
        digest.update(items[key].encode("utf-8"))
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------

def _conviction_verdicts() -> dict[str, frozenset[str]]:
    from detector.predicates import PREDICATE_CONVICTION_VERDICTS

    return dict(PREDICATE_CONVICTION_VERDICTS)


def measure_corpus(
    corpus_root: str | Path,
    predicates: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Run the convicting predicates over the labelled corpus, offline.

    Pure predicate evaluation: no provider, no judge, no defense, no gold ever
    entering a packet. Gold is read only to group the outcome.
    """
    from detector.dataset import load_case
    from detector.packet import build_packet
    from detector.predicates import run_predicate

    root = Path(corpus_root)
    conviction = _conviction_verdicts()
    names = list(predicates or sorted(conviction))
    counts: dict[str, dict[str, Any]] = {
        name: {
            "convicting_verdicts": sorted(conviction.get(name, ())),
            "clean_cases": 0,
            "clean_violations": 0,
            "clean_violation_cases": [],
            "error_cases": 0,
            "error_hits": 0,
            "error_hit_cases": [],
        }
        for name in names
    }
    totals = {
        "cases": 0, "clean_cases": 0, "error_cases": 0,
        "unlabelled_cases": 0, "unbuildable_cases": 0,
    }
    for path in iter_case_paths(root):
        relative = path.relative_to(root).as_posix()
        label = _case_label(path)
        totals["cases"] += 1
        if label is None:
            totals["unlabelled_cases"] += 1
            continue
        group = "clean" if label else "error"
        totals["clean_cases" if group == "clean" else "error_cases"] += 1
        try:
            record, _ = load_case(root, relative)
            packet = build_packet(record)
        except Exception:
            # A case that cannot be compiled is not a case the predicates
            # acquitted; it is a hole in the measurement, and admission refuses
            # while any exists.
            totals["unbuildable_cases"] += 1
            continue
        for name in names:
            bucket = counts[name]
            bucket["clean_cases" if group == "clean" else "error_cases"] += 1
            verdict = str(
                run_predicate(name, packet, {}).get("verdict") or ""
            )
            if verdict not in conviction.get(name, frozenset()):
                continue
            if group == "clean":
                bucket["clean_violations"] += 1
                bucket["clean_violation_cases"].append(relative)
            else:
                bucket["error_hits"] += 1
                bucket["error_hit_cases"].append(relative)
    return {"corpus": totals, "predicates": counts}


def build_artifact(
    corpus_root: str | Path = DEFAULT_CORPUS_ROOT,
    predicates: Sequence[str] | None = None,
    *,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """The calibration record: measurement + the fingerprints that bind it."""
    from datetime import datetime, timezone

    corpus = corpus_fingerprint(corpus_root)
    measurement = measure_corpus(corpus_root, predicates)
    unbuildable = int(
        (measurement.get("corpus") or {}).get("unbuildable_cases") or 0
    )
    entries: dict[str, Any] = {}
    for name, bucket in measurement["predicates"].items():
        admitted = (
            bucket["clean_violations"] == 0
            and bucket["clean_cases"] >= MIN_CLEAN_CASES
            and unbuildable == 0
        )
        entries[name] = {
            "clean_cases": bucket["clean_cases"],
            "clean_violations": bucket["clean_violations"],
            "clean_violation_cases": bucket["clean_violation_cases"],
            "error_cases": bucket["error_cases"],
            "error_hits": bucket["error_hits"],
            "error_hit_cases": bucket["error_hit_cases"],
            "convicting_verdicts": bucket["convicting_verdicts"],
            # Cases that could not be compiled are holes in the measurement, not
            # acquittals: a predicate is not admitted on a clean set that was
            # silently smaller than the corpus.
            "unbuildable_cases": unbuildable,
            "source_fingerprint": source_fingerprint(name),
            "admitted": admitted,
        }
    artifact = {
        "schema_version": SCHEMA,
        "metric_role": METRIC_ROLE,
        "generated_at_utc": generated_at_utc or datetime.now(timezone.utc).isoformat(),
        "minimum_clean_cases": MIN_CLEAN_CASES,
        "corpus": {
            "root": corpus["root"],
            **measurement["corpus"],
            "fingerprint": corpus["fingerprint"],
            "clean_fingerprint": corpus["clean_fingerprint"],
        },
        "predicates": entries,
    }
    artifact["content_digest"] = content_digest(artifact)
    return artifact


def content_digest(artifact: Mapping[str, Any]) -> str:
    """Digest of the record's meaning, excluding its own digest fields."""
    payload = {
        key: value for key, value in artifact.items()
        if key not in {"content_digest"}
    }
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, default=str,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Admission
# ---------------------------------------------------------------------------

class ConvictionCalibration:
    """The runtime view: is this predicate allowed to authorize a conviction?

    The record is read once and every predicate's status resolved once, so a
    batch of detections pays the measurement a single time. ``corpus_root`` and
    ``artifact`` are injectable because the tests build a real (tiny) corpus and
    a real record for it, rather than mocking the decision.
    """

    def __init__(
        self,
        *,
        artifact: str | Path | Mapping[str, Any] | None = None,
        corpus_root: str | Path | None = None,
        reverify: bool = True,
    ) -> None:
        self._artifact_source = artifact
        self.corpus_root = Path(corpus_root or DEFAULT_CORPUS_ROOT)
        self.reverify = reverify
        self._status: dict[str, dict[str, Any]] | None = None
        self._warnings: list[str] = []
        self._artifact_error = ""

    # -- record loading ----------------------------------------------------
    def _load_artifact(self) -> Mapping[str, Any] | None:
        source = self._artifact_source
        if source is None:
            source = DEFAULT_ARTIFACT
        if isinstance(source, Mapping):
            return source
        path = Path(source)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except OSError:
            self._artifact_error = "artifact_unavailable"
            return None
        except ValueError:
            self._artifact_error = "artifact_malformed"
            return None
        if not isinstance(payload, Mapping):
            self._artifact_error = "artifact_malformed"
            return None
        return payload

    # -- status ------------------------------------------------------------
    def status(self) -> dict[str, dict[str, Any]]:
        if self._status is None:
            self._status = self._resolve()
        return self._status

    def warnings(self) -> list[str]:
        """Things that could not be checked, as opposed to checks that failed.

        Kept apart from the refusal reasons and reported, never silently
        absorbed: a run on a machine without the (gitignored) corpus still
        convicts, and its report says the live re-measurement did not happen.
        """
        self.status()
        return list(self._warnings)

    def _resolve(self) -> dict[str, dict[str, Any]]:
        artifact = self._load_artifact()
        table = _conviction_verdicts()
        resolved: dict[str, dict[str, Any]] = {}
        if artifact is None:
            reason = self._artifact_error or "artifact_unavailable"
            self._warnings = [reason]
            return {
                name: {"admitted": False, "reasons": [reason], "detail": _REASONS[reason]}
                for name in table
            }
        corpus = self._corpus_state(artifact)
        live = measure_corpus(self.corpus_root) if self.reverify else None
        for name, verdicts in sorted(table.items()):
            reasons: list[str] = []
            entry = (artifact.get("predicates") or {}).get(name)
            if str(artifact.get("schema_version") or "") != SCHEMA:
                reasons.append("schema_mismatch")
            elif str(artifact.get("content_digest") or "") != content_digest(artifact):
                reasons.append("content_digest_mismatch")
            if not isinstance(entry, Mapping):
                reasons.append("predicate_not_recorded")
                resolved[name] = _decision(reasons, entry=None, measured=None)
                continue
            recorded_verdicts = sorted(str(item) for item in (
                entry.get("convicting_verdicts") or ()
            ))
            if recorded_verdicts != sorted(verdicts):
                reasons.append("conviction_verdicts_mismatch")
            if int(entry.get("clean_violations") or 0) != 0:
                reasons.append("clean_set_false_positive")
            if int(entry.get("clean_cases") or 0) < MIN_CLEAN_CASES:
                reasons.append("insufficient_clean_cases")
            if int(entry.get("unbuildable_cases") or 0) != 0:
                reasons.append("unbuildable_cases")
            reasons.extend(corpus["reasons"])
            if str(entry.get("source_fingerprint") or "") != source_fingerprint(name):
                reasons.append("source_fingerprint_mismatch")
            resolved[name] = _decision(
                reasons,
                entry=entry,
                measured=(live or {}).get("predicates", {}).get(name),
            )
        return resolved

    def _corpus_state(self, artifact: Mapping[str, Any]) -> dict[str, Any]:
        """The corpus checks that can be made, and the absence of the rest.

        Two levels, deliberately different:

        * A readable corpus must match the record. Any difference -- a changed
          file anywhere, a changed gold-clean subset -- refuses the authority,
          because the record then describes data that no longer exists.
        * An unreadable corpus is a *warning*, not a refusal. ``datasets/raw``
          is gitignored, so a checkout without it can still audit the record's
          provenance (source fingerprint, conviction vocabulary, recorded
          counts) but cannot re-measure. Recording that as silence would let a
          reader assume the re-measurement happened.
        """
        recorded = artifact.get("corpus") or {}
        if not str(recorded.get("fingerprint") or ""):
            return {"reasons": ["corpus_unavailable"], "live": None}
        root = self.corpus_root
        if not root.is_dir():
            self._warnings.append("corpus_unavailable")
            return {"reasons": [], "live": None}
        live = corpus_fingerprint(root)
        if int(live["cases"]) == 0:
            self._warnings.append("corpus_unavailable")
            return {"reasons": [], "live": None}
        if int(live["unlabelled_cases"]) != 0:
            self._warnings.append("unbuildable_cases")
        reasons: list[str] = []
        if live["fingerprint"] != str(recorded.get("fingerprint") or ""):
            reasons.append("corpus_fingerprint_mismatch")
        if live["clean_fingerprint"] != str(recorded.get("clean_fingerprint") or ""):
            reasons.append("clean_fingerprint_mismatch")
        return {"reasons": reasons, "live": live}

    # -- the question the guard asks ---------------------------------------
    def admits(self, predicate: str) -> tuple[bool, str]:
        status = self.status().get(predicate)
        if status is None:
            return False, "predicate_not_recorded"
        if status["admitted"]:
            return True, ""
        return False, status["reasons"][0]

    def summary(self) -> dict[str, Any]:
        status = self.status()
        return {
            "schema_version": SCHEMA,
            "artifact": (
                "<inline>" if isinstance(self._artifact_source, Mapping)
                else str(self._artifact_source or DEFAULT_ARTIFACT)
            ),
            "corpus_root": self.corpus_root.as_posix(),
            "reverified_at_runtime": bool(self.reverify),
            "admitted": sorted(
                name for name, item in status.items() if item["admitted"]
            ),
            "refused": {
                name: item["detail"] for name, item in status.items()
                if not item["admitted"]
            },
            "unavailable": list(self.warnings()),
        }


def _decision(
    reasons: Iterable[str],
    *,
    entry: Mapping[str, Any] | None = None,
    measured: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    unique = list(dict.fromkeys(reasons))
    if (
        isinstance(entry, Mapping)
        and measured is not None
        and int(entry.get("clean_violations") or 0) != int(
            measured.get("clean_violations") or 0
        )
    ):
        # The record says one thing and the live corpus says another. That can
        # only happen when the fingerprint check already refused (the digest and
        # the count describe the same bytes), but it is recorded separately so
        # the report names the count rather than only the digest.
        unique.append("remeasurement_disagreement")
    return {
        "admitted": not unique,
        "reasons": unique,
        "detail": "; ".join(_REASONS.get(reason, reason) for reason in unique),
    }


_CACHE: dict[tuple[str, str, bool], ConvictionCalibration] = {}


def default_calibration() -> ConvictionCalibration:
    """The process-wide calibration view used by the conviction guard."""
    key = (str(DEFAULT_ARTIFACT), str(DEFAULT_CORPUS_ROOT), True)
    if key not in _CACHE:
        _CACHE[key] = ConvictionCalibration()
    return _CACHE[key]


def reset_cache() -> None:
    """Drop the cached view. For tests and for a caller that re-measures."""
    _CACHE.clear()


__all__ = [
    "DEFAULT_ARTIFACT",
    "DEFAULT_CORPUS_ROOT",
    "MIN_CLEAN_CASES",
    "METRIC_ROLE",
    "SCHEMA",
    "ConvictionCalibration",
    "build_artifact",
    "content_digest",
    "corpus_fingerprint",
    "default_calibration",
    "dependency_items",
    "iter_case_paths",
    "measure_corpus",
    "reset_cache",
    "source_fingerprint",
]
