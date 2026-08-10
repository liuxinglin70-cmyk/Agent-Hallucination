"""Shared vocabulary constants for BCCSE rule and genome modules.

Why this module exists: genome.py and rule_contract.py both need the same
module/verdict/negation vocabularies, but genome.py also needs rule_contract's
RuleSpec for slot validation. Extracting the shared constants here breaks the
static import cycle without changing any runtime behavior.
"""

from __future__ import annotations

import re

MODULE_IDS = (
    "candidate_trigger",
    "evidence_obligation",
    "candidate_to_verifier_route",
    "atomic_verifier_instruction",
    "clean_defense_boundary",
    "causal_link_test",
    "earliest_origin_tiebreak",
    "abstention_rule",
)

ALLOWED_VERDICTS: dict[str, frozenset[str]] = {
    "abstention_rule": frozenset({"clean", "error", "unresolved"}),
    "clean_defense_boundary": frozenset({"clean"}),
}

LOCAL_NEGATION_MODULES: frozenset[str] = frozenset({"causal_link_test"})

# Single source of truth for the verdict-emission and local-negation
# vocabularies.  Both the free-text gate (genome.validate_runtime_rule) and
# the slot-contract gate (rule_contract.validate_rule_spec) enforce verdict
# permission with these exact patterns; a split vocabulary let a phrasing
# pass one gate and fail the other, silently changing module authority by
# rule form.  The set covers the English slot render plus the Chinese
# legacy free-text forms.
FINAL_VERDICT_RE = re.compile(
    r"判定为\s*(clean|error|unresolved)|"
    r"直接输出\s*(clean|error|unresolved)|"
    r"(?:return|output|classify as|label as)\s+(clean|error|unresolved)\b|"
    r"(?:treat|regard|consider|deem)\s+[^,，。；;]{0,40}\s+as\s+(clean|error|unresolved)\b|"
    r"(?:视为|视作|当作|认定为|判别为)\s*(clean|error|unresolved)|"
    r"应判(?:定为|为)?\s*(clean|error|unresolved)|"
    r"结论(?:为|是)\s*(clean|error|unresolved)",
    re.IGNORECASE,
)

LOCAL_NEGATION_RE = re.compile(
    r"\b(?:does|do|did|will|would|can|could|should|may|might) not hold\b|"
    r"不成立|不应视为|不足以构成|非因果|因果链不成立|不构成因果",
    re.IGNORECASE,
)

FORBIDDEN_CONTENT_MARKERS = frozenset({
    "case_id", "source_id", "agent_id", "model_id",
    "gold_explanation_text", "outer_metric", "outer_error",
    "example_specific_fact", "continuous_threshold_search",
})

INSTANCE_ID_PATTERN = r"\b(?:[a-z][a-z0-9]*[-_])+\d+\b"
INSTANCE_ID_RE = re.compile(INSTANCE_ID_PATTERN, re.IGNORECASE)

_FEATURE_POISON_PATTERNS = (
    r"https?://",
    r"\b(?:case|source|agent|model)[-_ ]?id\b",
    r"\b(?:case|source)[_-]\d+\b",
    r"\bag_\w{8,}\b",
    r"\b0x[0-9a-f]{6,}\b",
    r"\b(?:gold|ground[ -]?truth|golden label)\b",
    r"(?:金标|答案|正确标签|"
    r"真实标签|本案例|这个案例)",
    r"\b(?:ignore|disregard|override)\s+(?:all\s+)?"
    r"(?:previous|prior|above|system|developer)\b.{0,40}"
    r"\b(?:instruction|prompt|rule|message)s?\b",
    r"\b(?:system|developer|assistant)\s+(?:prompt|message)\b",
    r"\b(?:you are|follow these instructions|output only|return json)\b",
    r"\b(?:instruction|prompt)\s*:",
    r"^(?:ignore|follow|output|return|write|add|remove|modify|delete|always|never|must)\b",
    r"\b[a-z][a-z_]+/\d+\b",
    INSTANCE_ID_PATTERN,
)


def validate_feature_description(content: str) -> tuple[bool, str]:
    """Validate label-free feature provenance before it can reach prompts.

    Feature descriptions are persisted and later included in reflection
    context, so they need the same case/gold/URL boundary as runtime rules plus
    an explicit prompt-instruction boundary. Empty descriptions are allowed;
    the typed trajectory extractor can supply a safe fallback.
    """
    normalized = " ".join(str(content or "").split())
    if len(normalized) > 500:
        return False, "feature_description_too_long"
    lowered = normalized.lower()
    for marker in FORBIDDEN_CONTENT_MARKERS:
        if marker in lowered:
            return False, f"feature_forbidden_content:{marker}"
    for pattern in _FEATURE_POISON_PATTERNS:
        if re.search(pattern, lowered, flags=re.IGNORECASE | re.DOTALL):
            return False, f"feature_prompt_or_case_pollution:{pattern}"
    return True, ""


def semantic_overlap(text_a: str, text_b: str) -> float:
    """Compute language-agnostic character-bigram Jaccard similarity."""
    def tokens(text: str) -> set[str]:
        normalized = re.sub(r"[\W_]+", "", text.lower(), flags=re.UNICODE)
        if len(normalized) < 2:
            return {normalized} if normalized else set()
        return {
            normalized[index:index + 2]
            for index in range(len(normalized) - 1)
        }

    tokens_a = tokens(text_a)
    tokens_b = tokens(text_b)
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)
