"""Mechanical, label-free retrieval of external evidence into snapshots.

This module is the retrieval step the earlier diagnostic lacked: snapshots
are now *selected and assembled by rule* from a document corpus, not
hand-picked. Three properties are deliberate.

- **Label-free.** Queries are derived from the question text and, when a
  claim is supplied, from that claim. Gold labels, reference answers and
  label reasons never enter.
- **Deterministic.** Scoring, passage extraction, value extraction and
  conflict adjudication are pure functions of the corpus and the question,
  so the same input always yields the same snapshot digest.
- **Fail-safe with a reason.** When nothing in the corpus bears on the case,
  ``build_case_snapshot`` returns ``None`` plus a reason, and the detector
  simply runs without a snapshot. A coverage gap is reported, never patched
  over by loosening a threshold or voting.

Adjudication follows the same law as the snapshot schema: a value conflict
is settled only when one source matches strictly more of the task's own
descriptors than the others. A tie stays unresolved, and the stages must
then report the question undecided.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from detector.evidence import build_snapshot, excerpt_sha256

CORPUS_SCHEMA = "detector.corpus.v1"

MIN_MATCHED_TERMS = 2
MAX_SOURCES_PER_SNAPSHOT = 4
MAX_PASSAGE_CHARS = 900
DEFAULT_CLAIM_CHARS = 400

_STOPWORDS = frozenset("""
a an the and or but if then than that this these those of in on at to for from by
with without into over under about as is are was were be been being do does did
doing have has had having it its it's their there here what which who whom whose
when where why how many much name called release released original first
""".split())

_QUESTION_WORDS = frozenset({
    "how", "what", "which", "who", "whom", "whose", "when", "where", "why",
})

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’\u2019]*")
_QUOTED_RE = re.compile(r"[\"'\u201c\u2018]([^\"'\u201c\u201d\u2018\u2019]{2,80})[\"'\u201d\u2019]")
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_UNITS = frozenset({
    "ton", "tonne", "kilogram", "gram", "pound", "metre", "meter", "mile",
    "corner", "goal", "point", "minute", "second", "hour", "day", "year",
    "percent", "dollar", "euro",
})

_TOP_LEVEL_KEYS = frozenset({"schema_version", "documents"})
_DOCUMENT_KEYS = frozenset({
    "doc_id", "path", "url", "title", "publisher", "retrieved_at",
    "source_kind", "derivation",
})


class CorpusError(ValueError):
    """The corpus cannot be used for retrieval."""


def _stem(token: str) -> str:
    """A deliberately light stem, enough to group ``tons`` with ``ton``."""
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    """Lowercase content tokens, stopwords dropped, light stemming applied."""
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(str(text or "")):
        token = raw.casefold().strip("'’-")
        if not token:
            continue
        if token.isdigit():
            tokens.append(token)
            continue
        if token in _STOPWORDS or len(token) < 3:
            continue
        tokens.append(_stem(token))
    return tokens


def derive_query(question: str, claim: str | None = None) -> list[str]:
    """Order-preserving distinct terms from the question (and optional claim).

    The claim is truncated so a long stage output cannot flood the query;
    the question keeps its own terms first.
    """
    parts = [str(question or "")]
    if claim:
        parts.append(str(claim)[:DEFAULT_CLAIM_CHARS])
    seen: set[str] = set()
    terms: list[str] = []
    for part in parts:
        for term in tokenize(part):
            if term in seen:
                continue
            seen.add(term)
            terms.append(term)
    return terms


def case_entity(question: str) -> str | None:
    """The case's subject: a quoted name, else the longest capitalised run.

    Both are label-free text properties of the question. Returns ``None``
    when the question names no subject, in which case retrieval abstains
    rather than guessing.
    """
    quoted = [
        hit.strip(" .,;:!?-—")
        for hit in _QUOTED_RE.findall(str(question or ""))
    ]
    quoted = [hit for hit in quoted if len(hit) >= 2]
    if quoted:
        return max(quoted, key=len)
    runs: list[tuple[int, list[str]]] = []
    current: list[str] = []
    started_at = 0
    for index, raw in enumerate(_TOKEN_RE.findall(str(question or ""))):
        stripped = raw.strip("'’-")
        if (stripped[:1].isupper() and not stripped.isupper()
                and stripped.casefold() not in _STOPWORDS
                and stripped.casefold() not in _QUESTION_WORDS):
            if not current:
                started_at = index
            current.append(stripped)
            continue
        if current:
            runs.append((started_at, current))
            current = []
    if current:
        runs.append((started_at, current))
    # A single capitalised word is a name only when it is not merely the
    # sentence's first word: "Given this table" is not a subject to look up,
    # while "the Kashmir question" is. Multi-word runs are unambiguous.
    candidates = [
        run for start, run in runs
        if not (len(run) == 1 and start == 0)
        and len(" ".join(run)) >= 3
    ]
    if not candidates:
        return None
    longest = max(candidates, key=lambda run: (len(run), -len(" ".join(run))))
    return " ".join(longest)


def load_corpus(directory: str | Path) -> dict[str, Any]:
    """Load and validate a ``detector.corpus.v1`` manifest."""
    root = Path(directory).resolve()
    manifest_path = root / "corpus.json"
    if not manifest_path.is_file():
        raise CorpusError(f"corpus manifest not found: {manifest_path}")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise CorpusError("corpus manifest must be an object")
    extra = sorted(str(key) for key in payload if str(key) not in _TOP_LEVEL_KEYS)
    if extra:
        raise CorpusError(f"corpus manifest carries unknown fields: {extra}")
    if str(payload.get("schema_version") or "") != CORPUS_SCHEMA:
        raise CorpusError(
            f"unsupported corpus schema: {payload.get('schema_version')!r}"
        )
    documents = payload.get("documents")
    if not isinstance(documents, list) or not documents:
        raise CorpusError("corpus documents must be a non-empty list")
    loaded: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(documents, start=1):
        where = f"documents[{index}]"
        if not isinstance(raw, Mapping):
            raise CorpusError(f"{where} must be an object")
        unknown = sorted(str(key) for key in raw if str(key) not in _DOCUMENT_KEYS)
        if unknown:
            raise CorpusError(f"{where} carries unknown fields: {unknown}")
        doc_id = str(raw.get("doc_id") or "").strip()
        if not doc_id or doc_id in seen_ids:
            raise CorpusError(f"{where}.doc_id must be non-empty and unique")
        seen_ids.add(doc_id)
        relative = Path(str(raw.get("path") or ""))
        if not str(relative) or relative.is_absolute() or ".." in relative.parts:
            raise CorpusError(f"{where}.path must stay inside the corpus")
        target = (root / relative).resolve()
        if root not in target.parents and target != root:
            raise CorpusError(f"{where}.path escapes the corpus directory")
        if not target.is_file():
            raise CorpusError(f"{where}.path does not exist: {relative}")
        url = str(raw.get("url") or "")
        if not url.startswith(("http://", "https://")):
            raise CorpusError(f"{where}.url must be http(s)")
        loaded.append({
            "doc_id": doc_id,
            "path": str(relative),
            "url": url,
            "title": str(raw.get("title") or doc_id),
            "publisher": str(raw.get("publisher") or ""),
            "retrieved_at": str(raw.get("retrieved_at") or ""),
            "source_kind": str(raw.get("source_kind") or "text"),
            "derivation": str(raw.get("derivation") or ""),
            "text": target.read_text(encoding="utf-8", errors="replace"),
        })
    return {"root": root, "documents": loaded}


def _document_frequency(terms: Sequence[str], documents: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    frequency: dict[str, int] = {}
    for document in documents:
        present = set(tokenize(document["text"]))
        for term in set(terms):
            if term in present:
                frequency[term] = frequency.get(term, 0) + 1
    return frequency


def score_documents(
    terms: Sequence[str],
    documents: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Rank documents by rare matched terms; deterministic ordering."""
    frequency = _document_frequency(terms, documents)
    total = max(1, len(documents))
    scored: list[dict[str, Any]] = []
    for document in documents:
        present = set(tokenize(document["text"]))
        matched = [term for term in dict.fromkeys(terms) if term in present]
        if len(matched) < MIN_MATCHED_TERMS:
            continue
        score = 0.0
        for term in matched:
            score += 1.0 + (total / max(1, frequency.get(term, 1)))
        scored.append({
            "document": document,
            "matched_terms": matched,
            "score": round(score, 6),
        })
    scored.sort(key=lambda row: (-row["score"], str(row["document"]["doc_id"])))
    return scored


def _is_record_listing(lines: Sequence[str]) -> bool:
    """Whether the text is one record per line rather than wrapped prose.

    A flattening of a structured reply has a key or an index on nearly every
    line; wrapped prose does not, however short its lines are. Judging by
    the shape keeps a book paragraph intact instead of shredding it into
    single lines that no longer state what the paragraph states.
    """
    if len(lines) < 3:
        return False
    record_like = sum(
        1 for line in lines
        if re.match(r"^[^\s:][^:\n]{0,89}:\s", line)
        or re.match(r"^\d{1,4}[.)]\s", line)
    )
    return record_like / len(lines) >= 0.6


def _paragraphs(text: str) -> list[str]:
    """Paragraphs, or records when the text is a table-like listing.

    A flattened API reply or a tracklist has one record per line; splitting
    it into blank-line blocks would hide a record inside a wall of text, so
    line-delimited text is treated as one record per line.
    """
    raw = str(text or "")
    chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n", raw) if chunk.strip()]
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not lines:
        return []
    if _is_record_listing(lines):
        return lines
    return chunks or lines


def _record_block(lines: Sequence[str], index: int) -> str:
    """The contiguous lines sharing the chosen line's record prefix."""
    prefix_part = lines[index].split(":", 1)[0]
    if "/" not in prefix_part:
        return lines[index]
    prefix = prefix_part.rsplit("/", 1)[0]
    block = [
        line for line in lines if line.split(":", 1)[0].startswith(prefix + "/")
    ]
    return "\n".join(block)


def _record_stating_number(
    lines: Sequence[str], asked: Sequence[str],
) -> str | None:
    """The record whose own field equals the asked number, as a block.

    A field equal to the asked number (``position: 12``, ``12. Title``) is
    the record the question is about; a line that merely contains the digits
    elsewhere (an id, a duration) is not. When several records carry the
    same index, the first with a name field wins, in line order.
    """
    for index, line in enumerate(lines):
        path, separator, value = line.partition(":")
        if separator and value.strip().rstrip(".") in asked:
            block = _record_block(lines, index)
            fields = {
                item.split(":", 1)[0].rsplit("/", 1)[-1].strip()
                for item in block.splitlines() if ":" in item
            }
            # A record identified only by a counter has no name to answer
            # with; only a record carrying a name field can be the answer.
            if set(_NAME_FIELDS) & fields:
                return block
        numbered = re.match(r"^(\d{1,4})[.)]\s+(.*)$", line)
        if numbered and numbered.group(1) in asked:
            return line
    return None


def best_passage(
    text: str,
    terms: Sequence[str],
    question: str = "",
    *,
    limit: int = MAX_PASSAGE_CHARS,
) -> tuple[str, list[str]]:
    """The passage answering the question, by term coverage and asked number.

    Selection order is fixed: a passage that states the number the question
    asks about outranks one that merely shares vocabulary (a tracklist entry
    outranks the album heading); within each class the most query terms
    wins, and ties resolve to the earliest passage, so the choice never
    depends on iteration order of a set.
    """
    wanted = list(dict.fromkeys(terms))
    paragraphs = _paragraphs(text)
    asked = _asked_numbers(question)
    record_like = _is_record_listing([line for line in str(text).splitlines() if line.strip()])
    if record_like and asked and _value_mode(question) == "label":
        stated = _record_stating_number(paragraphs, asked)
        if stated is not None:
            return stated, [term for term in wanted
                            if term in set(tokenize(stated))]
    best: tuple[int, int, int, list[str], str] | None = None
    for index, paragraph in enumerate(paragraphs):
        present = set(tokenize(paragraph))
        hits = [term for term in wanted if term in present]
        if not hits:
            continue
        states_asked = 0
        if asked:
            body = paragraph
            if record_like:
                body = _record_block(paragraphs, index)
            tokens = set(tokenize(body))
            states_asked = 1 if any(number in tokens for number in asked) else 0
        candidate = (states_asked, len(hits), -index, hits, paragraph)
        if best is None or candidate[:3] > best[:3]:
            best = candidate
    if best is None:
        return "", []
    _, _, _, best_hits, paragraph = best
    if record_like:
        index = paragraphs.index(paragraph)
        paragraph = _record_block(paragraphs, index)
    if len(paragraph) <= limit:
        return paragraph, best_hits
    position = 0
    for term in best_hits:
        found = paragraph.casefold().find(term)
        if found >= 0:
            position = found
            break
    start = max(0, min(position - limit // 3, len(paragraph) - limit))
    window = paragraph[start:start + limit].strip()
    return window, [term for term in best_hits if term in set(tokenize(window))]


def _asked_numbers(question: str) -> list[str]:
    """Standalone numbers the question asks about (a track index, a year)."""
    return [
        number.replace(",", "")
        for number in _NUMBER_RE.findall(str(question or ""))
    ]


def question_attribute(question: str) -> str | None:
    """The property the question asks for, derived from the question alone.

    ``how many/much X`` and ``name of X`` name their property directly; a
    bare interrogative falls back to the first content token after it. One
    stable attribute per question keeps two sources on the same property,
    so a genuine disagreement shows up as a conflict instead of silently
    splitting into two different attributes.
    """
    tokens = [
        _stem(raw.strip("'’-").casefold())
        for raw in _TOKEN_RE.findall(str(question or ""))
    ]
    if _value_mode(question) == "label":
        # "the name of track 12" asks for the property `track`: take the
        # content token the asked number attaches to.
        for index, token in enumerate(tokens):
            if not token.isdigit():
                continue
            for candidate in reversed(tokens[max(0, index - 4):index]):
                if candidate in _QUESTION_WORDS or candidate in _STOPWORDS:
                    continue
                return candidate
    for index, token in enumerate(tokens):
        if token in {"how", "what", "which", "who", "whose", "when", "where"}:
            for following in tokens[index + 1:index + 4]:
                if following in _QUESTION_WORDS or following in _STOPWORDS:
                    continue
                if following.isdigit():
                    continue
                return following
    return None


def _interrogative_index(question: str) -> int:
    tokens = tokenize(question)
    for index, token in enumerate(tokens):
        if token in _QUESTION_WORDS:
            return index
    return 0


def _value_mode(question: str) -> str:
    """``label`` when the task asks for a name, ``number`` otherwise."""
    lowered = str(question or "").casefold()
    if any(marker in lowered for marker in (
        "name of", "name is", "is the name", "title", "called", "which song",
        "what song", "which track", "what track",
    )):
        return "label"
    return "number"


def _nearest_question_term(
    question: str,
    passage: str,
    banned: set[str],
) -> str | None:
    """The question's earliest content token that appears in the passage."""
    present = set(tokenize(passage))
    start = _interrogative_index(question)
    tokens = tokenize(question)
    for token in tokens[start:]:
        if token in banned or token in _QUESTION_WORDS:
            continue
        if token in present:
            return token
    for token in tokens:
        if token in banned or token in _QUESTION_WORDS:
            continue
        if token in present:
            return token
    return None


def _unit_for(token: str) -> str | None:
    return token if token in _UNITS else None


def _number_value(
    passage: str,
    attribute: str,
    *,
    question_numbers: set[str],
) -> str | None:
    """A number appearing near the attribute token, with its unit if any.

    Numbers the question itself states are descriptors (a build year, a
    track index), never the value being looked up, so they are skipped.
    """
    lowered = passage.casefold()
    anchor = lowered.find(attribute)
    if anchor < 0:
        return None
    best: tuple[int, str] | None = None
    for match in _NUMBER_RE.finditer(lowered):
        number = match.group(0).replace(",", "")
        if number in question_numbers:
            continue
        context = lowered[max(0, match.start() - 48):match.end() + 48]
        context_tokens = [_stem(token.strip("'’-"))
                          for token in _TOKEN_RE.findall(context)]
        if attribute not in context_tokens:
            continue
        following = _TOKEN_RE.findall(lowered[match.end():match.end() + 24])
        unit = _unit_for(_stem(following[0])) if following else None
        value = f"{number} {unit}" if unit else number
        distance = abs(match.start() - anchor)
        if best is None or distance < best[0]:
            best = (distance, value)
    return best[1] if best else None


def _record_name_for_asked(
    passage: str,
    asked: Sequence[str],
) -> str | None:
    """The name field of the record whose own field equals the asked number.

    Reading the value out of the record the number identifies keeps a
    listing's other records (and its container's title) from being mistaken
    for the answer.
    """
    lines = [line.strip() for line in str(passage or "").splitlines() if line.strip()]
    prefix: str | None = None
    for line in lines:
        path, separator, value = line.partition(":")
        if separator and value.strip().rstrip(".") in asked:
            prefix = path.rsplit("/", 1)[0]
            break
    if prefix is None:
        return None
    for field in _NAME_FIELDS:
        for line in lines:
            path, separator, value = line.partition(":")
            if not separator or not value.strip():
                continue
            if path.startswith(prefix + "/") and path.rsplit("/", 1)[-1] == field:
                return value.strip()
    return None


def _label_value(
    passage: str,
    question: str,
    attribute: str,
) -> str | None:
    """A quoted title near the attribute, else the record the id points to."""
    quoted = _QUOTED_RE.findall(passage)
    if quoted:
        chosen = max(quoted, key=len).strip()
        if chosen.casefold() not in _STOPWORDS and len(chosen) >= 3:
            return chosen
    named = _record_name_for_asked(passage, _asked_numbers(question))
    if named:
        return named
    numbers = [number for number in _NUMBER_RE.findall(question)]
    for number in numbers:
        for match in _number_mentions(passage, number):
            candidate = _value_after_number(passage[match.end():])
            if candidate:
                return candidate
    tokens = _TOKEN_RE.findall(passage)
    for index, raw in enumerate(tokens):
        if _stem(raw.casefold()) == attribute and index + 1 < len(tokens):
            return " ".join(tokens[index + 1:index + 4])
    return None


def _number_mentions(passage: str, number: str) -> list[re.Match[str]]:
    """Every standalone mention of the asked number (not part of a longer one)."""
    pattern = r"(?<![0-9.])" + re.escape(number) + r"(?![0-9])"
    return list(re.finditer(pattern, passage))


_NAME_FIELDS = ("title", "name", "label")


def _record_field_value(tail: str) -> str | None:
    """A neighbouring field of the record carrying a name, by fixed order.

    Flattened listings put a record's fields on consecutive lines, so the
    name lives on a sibling line rather than in the text right after the
    number. Lookup is by line prefix and by the field names above, so the
    same listing always yields the same value.
    """
    lines = [line.strip() for line in str(tail or "").splitlines()]
    prefix: str | None = None
    for line in lines:
        path, separator, value = line.partition(":")
        if not separator:
            continue
        if value.strip().isdigit():
            prefix = path.rsplit("/", 1)[0]
            break
    if not prefix:
        return None
    found: dict[str, str] = {}
    for line in lines:
        path, separator, value = line.partition(":")
        if not separator or not path.startswith(prefix + "/"):
            continue
        key = path.rsplit("/", 1)[-1]
        if key in _NAME_FIELDS and value.strip():
            found.setdefault(key, value.strip())
    for field in _NAME_FIELDS:
        if field in found:
            return found[field]
    return None


def _value_after_number(tail: str) -> str | None:
    """The value a record or clause carries right after the asked number.

    Handles a record field (``... | title: Funky Radio | ...``) and a prose
    clause (``12. Funky Radio`` / ``12: "Funky Radio"``) without assuming
    either shape beyond the separators themselves.
    """
    text = tail.strip()
    if not text:
        return None
    if len(text) > 1 and text[0] in ":.)]":
        text = text[1:].strip()
    quoted = _QUOTED_RE.search(text[:120])
    if quoted and len(quoted.group(1).strip()) >= 3:
        return quoted.group(1).strip()
    record = _record_field_value(text)
    if record:
        return record
    segments = re.split(r"\s*(?:[|;]|\n|\u2014|\(|\[)\s*", text)
    for segment in segments[:4]:
        cleaned = segment.strip(" .-\"'")
        if not cleaned:
            continue
        key, separator, remainder = cleaned.partition(":")
        if separator and re.fullmatch(
            r"[A-Za-z][A-Za-z0-9 _\-]{1,24}", key.strip(),
        ) and remainder.strip():
            cleaned = remainder.strip().strip(" .-\"'")
        if len(cleaned) < 3 or cleaned.isdigit():
            continue
        if re.fullmatch(r"[\w./\[\]-]+", cleaned) and "/" in cleaned:
            continue
        return cleaned
    return None


def _mentions(text: str, entity: str) -> bool:
    """Whole-word, whitespace-tolerant mention of an entity name."""
    tokens = [re.escape(part) for part in str(entity).split() if part]
    if not tokens:
        return False
    pattern = (
        r"(?<![A-Za-z0-9])" + r"[\s_]+".join(tokens) + r"(?![A-Za-z0-9])"
    )
    return re.search(pattern, str(text or ""), re.IGNORECASE) is not None


def extract_verifies(
    question: str,
    passage: str,
    document_title: str = "",
) -> tuple[dict[str, str] | None, str]:
    """The (entity, attribute, value) the passage states, by rule.

    The case entity must be linked to the passage: either the passage names
    it, or the document's title does and the passage is one of its records.
    The second form is recorded in the source's title and context, so an
    auditor can check the link. Returns ``(verifies, reason)``; ``None``
    means the passage states no usable value and the document is dropped.
    """
    entity = case_entity(question)
    if not entity:
        return None, "the question names no subject to look up"
    in_passage = _mentions(passage, entity)
    in_title = bool(document_title) and _mentions(str(document_title), entity)
    if not (in_passage or in_title):
        return None, f"passage does not mention the case entity {entity!r}"
    banned = {_stem(token) for token in tokenize(entity)}
    attribute = question_attribute(question)
    if not attribute or attribute in banned:
        attribute = _nearest_question_term(question, passage, banned)
    if not attribute:
        return None, "passage shares no attribute term with the question"
    if attribute not in set(tokenize(passage)) and attribute != _stem(
        str(attribute)
    ):
        return None, f"passage does not state the asked property {attribute!r}"
    mode = _value_mode(question)
    if mode == "label":
        value = _label_value(passage, question, attribute)
    else:
        value = _number_value(
            passage, attribute,
            question_numbers={n.replace(",", "") for n in _NUMBER_RE.findall(question)},
        )
    if not value:
        return None, (
            f"no {mode} value found near the attribute {attribute!r}"
        )
    return {
        "entity": entity,
        "attribute": attribute,
        "value": str(value),
    }, ""


def _descriptor_terms(question: str, entity: str) -> list[str]:
    banned = {_stem(token) for token in tokenize(entity)}
    return [
        term for term in derive_query(question)
        if term not in banned and not term.isdigit()
    ]


def _mechanical_resolutions(
    question: str,
    sources: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Settle a conflict only when one source strictly dominates on descriptors.

    The task's own words are the arbiter: the source that matches more of
    the question's descriptors is the one the task asks about. A tie, or a
    dominance smaller than one descriptor, stays unresolved.
    """
    from detector.evidence import conflict_report

    entity = case_entity(question) or ""
    descriptors = _descriptor_terms(question, entity)
    if len(descriptors) < 2:
        return []
    coverage: dict[str, int] = {}
    matched: dict[str, list[str]] = {}
    for source in sources:
        text = f"{source['excerpt']} {source['applicable_context']}"
        present = set(tokenize(text))
        hits = [term for term in descriptors if term in present]
        coverage[str(source["evidence_id"])] = len(hits)
        matched[str(source["evidence_id"])] = hits
    resolutions: list[dict[str, Any]] = []
    for conflict in conflict_report(sources):
        key_ids = [str(item) for item in conflict["evidence_ids"]]
        ranked = sorted(key_ids, key=lambda item: (-coverage[item], item))
        if len(ranked) < 2:
            continue
        winner, runner_up = ranked[0], ranked[1]
        if coverage[winner] <= coverage[runner_up]:
            continue
        resolutions.append({
            "entity": conflict["entity"],
            "attribute": conflict["attribute"],
            "resolved_by": winner,
            "reason": (
                "The task's own descriptors decide applicability: "
                f"{winner} matches {coverage[winner]} of them "
                f"({', '.join(matched[winner][:8])}) while {runner_up} "
                f"matches {coverage[runner_up]} "
                f"({', '.join(matched[runner_up][:8]) or 'none'}). "
                "Repetition and gallery captions were not counted."
            ),
        })
    return resolutions


def build_case_snapshot(
    corpus: Mapping[str, Any],
    question: str,
    *,
    claim_text: str | None = None,
    top_k: int = MAX_SOURCES_PER_SNAPSHOT,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Retrieve, extract and assemble one snapshot for a case.

    Returns ``(snapshot_or_None, report)``. The report always carries the
    derived query and the per-document decision, so an auditor can see why
    a case has no snapshot instead of guessing.
    """
    terms = derive_query(question, claim_text)
    report: dict[str, Any] = {
        "query_terms": terms,
        "entity": case_entity(question),
        "value_mode": _value_mode(question),
        "documents": [],
    }
    if not terms:
        report["outcome"] = "no query terms derived from the question"
        return None, report
    ranked = score_documents(terms, corpus["documents"])
    if not ranked:
        report["outcome"] = (
            f"no corpus document matched at least {MIN_MATCHED_TERMS} "
            "question terms"
        )
        return None, report
    sources: list[dict[str, Any]] = []
    seen_excerpts: set[str] = set()
    for row in ranked[:top_k]:
        document = row["document"]
        passage, hits = best_passage(document["text"], terms, question)
        decision: dict[str, Any] = {
            "doc_id": document["doc_id"],
            "matched_terms": row["matched_terms"],
            "passage_terms": hits,
        }
        if not passage:
            decision["decision"] = "no paragraph matched a query term"
            report["documents"].append(decision)
            continue
        verifies, reason = extract_verifies(
            question, passage, str(document.get("title") or ""),
        )
        if verifies is None:
            decision["decision"] = f"dropped: {reason}"
            report["documents"].append(decision)
            continue
        digest = excerpt_sha256(passage)
        if digest in seen_excerpts:
            decision["decision"] = "dropped: identical passage already kept"
            report["documents"].append(decision)
            continue
        seen_excerpts.add(digest)
        decision["decision"] = "kept"
        decision["verifies"] = verifies
        decision["excerpt_sha256"] = digest
        report["documents"].append(decision)
        sources.append({
            "evidence_id": f"E{len(sources) + 1}",
            "url": document["url"],
            "title": document["title"],
            "publisher": document["publisher"] or document["doc_id"],
            "retrieved_at": document["retrieved_at"],
            "excerpt": passage,
            "verifies": verifies,
            "applicable_context": (
                "Passage selected by lexical retrieval from "
                f"{document['doc_id']} for the query terms "
                f"{', '.join(terms[:10])}"
                + (
                    "; the case entity is named by the document title"
                    if case_entity(question)
                    and case_entity(question).casefold()
                    not in passage.casefold()
                    else ""
                )
                + (f"; {document['derivation']}" if document["derivation"] else "")
            ),
        })
    if not sources:
        report["outcome"] = "the corpus matched but stated no usable value"
        return None, report
    resolutions = _mechanical_resolutions(question, sources)
    if resolutions:
        report["resolutions"] = resolutions
    snapshot = build_snapshot(
        query=" ".join(terms[:16]),
        derivation="task" if claim_text is None else "task+claim",
        task_excerpt=str(question)[:400],
        claim=(str(claim_text)[:300] if claim_text else None),
        sources=sources,
        resolutions=resolutions or None,
    )
    report["outcome"] = f"snapshot with {len(sources)} source(s)"
    report["snapshot_digest"] = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:16]
    return snapshot, report


def main(argv: Iterable[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Build one evidence snapshot for a case by mechanical retrieval.",
    )
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--question", required=True,
                        help="the case question text (label-free)")
    parser.add_argument("--claim", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(list(argv) if argv is not None else None)
    corpus = load_corpus(args.corpus)
    snapshot, report = build_case_snapshot(corpus, args.question,
                                           claim_text=args.claim)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if snapshot is None:
        return 1
    if args.out:
        Path(args.out).write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return 0


__all__ = [
    "CORPUS_SCHEMA",
    "CorpusError",
    "best_passage",
    "build_case_snapshot",
    "case_entity",
    "derive_query",
    "extract_verifies",
    "load_corpus",
    "score_documents",
    "tokenize",
]


if __name__ == "__main__":
    raise SystemExit(main())
