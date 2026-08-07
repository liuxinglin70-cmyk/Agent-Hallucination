"""CESV v4.0 — Causal Evidential Sequential Verification contract.

Core algorithmic module implementing the three CESV innovations:
1. Typed Provenance Graph with deterministic edge classification (7 edge types)
2. Dirichlet evidential uncertainty decomposition (aleatoric/epistemic separation)
3. Heuristic sequential evidence aggregation for auxiliary ranking

This module is purely deterministic except for the evidential scoring interface
which consumes LLM-produced Dirichlet parameters. All causal reasoning is done
by graph algorithms, NOT by LLM judgment.

Design references:
- CHIEF (Wang et al., 2026): typed causal graph + counterfactual attribution
- E-valuator (Sadhuka et al., 2025): sequential e-value hypothesis testing
- LogTokU (Ma et al., 2025): Dirichlet evidence decomposition
- Provenance Survey (Wang/Griffith, 2026): 7 typed provenance relations
"""

from __future__ import annotations

import math
import re
from collections import deque
from enum import Enum
from typing import Any


# ============================================================
# Phase 1: Typed Provenance Graph
# ============================================================

class EdgeType(str, Enum):
    """Seven typed causal/provenance relations (Griffith 2026 taxonomy)."""
    SUPPORT = "SUPPORT"           # step_j output correctly referenced by step_k
    CONTRADICT = "CONTRADICT"     # step_j output contradicts step_k's claim
    INVALIDATE = "INVALIDATE"     # step_j failure invalidates step_k's premise
    TRIGGER = "TRIGGER"           # step_j execution triggers step_k
    OVERRIDE = "OVERRIDE"         # step_k explicitly corrects step_j (recovery)
    PROPAGATE = "PROPAGATE"       # step_j error info passed unchanged by step_k
    INDEPENDENT = "INDEPENDENT"   # no causal relationship


class ProvenanceEdge:
    """A typed directed edge in the provenance graph."""

    __slots__ = ("source", "target", "edge_type", "evidence")

    def __init__(self, source: str, target: str, edge_type: EdgeType, evidence: str = ""):
        self.source = source
        self.target = target
        self.edge_type = edge_type
        self.evidence = evidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "edge_type": self.edge_type.value,
            "evidence": self.evidence,
        }


class ProvenanceGraph:
    """Typed directed acyclic graph over trajectory events.

    Nodes are event_ids. Edges represent deterministic causal/provenance
    relations classified by code logic (string matching, tool chain analysis).
    """

    def __init__(self) -> None:
        self.nodes: list[str] = []
        self.edges: list[ProvenanceEdge] = []
        self._adjacency: dict[str, list[ProvenanceEdge]] = {}  # outgoing
        self._in_adjacency: dict[str, list[ProvenanceEdge]] = {}  # incoming
        self._node_set: set[str] = set()

    def add_node(self, event_id: str) -> None:
        if event_id not in self._node_set:
            self._node_set.add(event_id)
            self.nodes.append(event_id)
            self._adjacency.setdefault(event_id, [])
            self._in_adjacency.setdefault(event_id, [])

    def add_edge(self, source: str, target: str, edge_type: EdgeType, evidence: str = "") -> None:
        self.add_node(source)
        self.add_node(target)
        edge = ProvenanceEdge(source, target, edge_type, evidence)
        self.edges.append(edge)
        self._adjacency[source].append(edge)
        self._in_adjacency[target].append(edge)

    def outgoing(self, node: str) -> list[ProvenanceEdge]:
        return self._adjacency.get(node, [])

    def incoming(self, node: str) -> list[ProvenanceEdge]:
        return self._in_adjacency.get(node, [])

    def downstream_nodes(self, node: str) -> set[str]:
        """All nodes reachable from `node` via any edge type."""
        visited: set[str] = set()
        queue = deque([node])
        while queue:
            current = queue.popleft()
            for edge in self._adjacency.get(current, []):
                if edge.target not in visited:
                    visited.add(edge.target)
                    queue.append(edge.target)
        return visited

    def edge_type_distribution(self) -> dict[str, int]:
        dist: dict[str, int] = {}
        for edge in self.edges:
            dist[edge.edge_type.value] = dist.get(edge.edge_type.value, 0) + 1
        return dist

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": self.nodes,
            "edges": [e.to_dict() for e in self.edges],
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "edge_type_distribution": self.edge_type_distribution(),
        }


# --- Deterministic Edge Classification Logic ---

def _extract_tool_outputs(event: dict[str, Any]) -> list[str]:
    """Extract textual tool outputs from an event."""
    outputs = []
    for interaction in event.get("tool_interactions") or []:
        obs = interaction.get("observation")
        if obs is not None:
            outputs.append(str(obs)[:2000])
    return outputs


def _extract_content(event: dict[str, Any]) -> str:
    """Extract main textual content from an event."""
    content = event.get("content")
    return str(content)[:3000] if content else ""


def _extract_tool_names(event: dict[str, Any]) -> list[str]:
    """Extract tool names used in an event."""
    return [
        str(interaction.get("tool_name") or "")
        for interaction in event.get("tool_interactions") or []
    ]


def _has_tool_error(event: dict[str, Any]) -> bool:
    """Check if any tool interaction in this event returned an error."""
    error_patterns = ("error", "exception", "traceback", "failed", "not found", "invalid")
    for interaction in event.get("tool_interactions") or []:
        obs = str(interaction.get("observation") or "").lower()
        if any(p in obs for p in error_patterns):
            return True
    return False


def _content_overlap(text_a: str, text_b: str, min_overlap: int = 20) -> float:
    """Compute token-level overlap ratio between two texts."""
    if not text_a or not text_b:
        return 0.0
    tokens_a = set(text_a.lower().split())
    tokens_b = set(text_b.lower().split())
    if not tokens_a or not tokens_b:
        return 0.0
    overlap = tokens_a & tokens_b
    # Remove very common tokens
    stopwords = {"the", "a", "an", "is", "are", "was", "were", "to", "of", "in", "for", "and", "or", "it", "this", "that"}
    overlap -= stopwords
    return len(overlap) / min(len(tokens_a), len(tokens_b))


def _has_contradiction_signal(text_a: str, text_b: str) -> bool:
    """Detect contradiction signals between two texts."""
    negation_patterns = [
        r"\bnot\b.*\b(correct|right|true|valid)\b",
        r"\bincorrect\b", r"\bwrong\b", r"\bfalse\b",
        r"\bhowever\b.*\b(actually|in fact)\b",
        r"\bcontradicts?\b", r"\bdisagrees?\b",
        r"\binstead\b.*\b(should|must|need)\b",
    ]
    combined = (text_a + " " + text_b).lower()
    return any(re.search(p, combined) for p in negation_patterns)


def _is_retry_with_correction(event_j: dict[str, Any], event_k: dict[str, Any]) -> bool:
    """Detect if event_k is a retry/correction of event_j (OVERRIDE)."""
    tools_j = _extract_tool_names(event_j)
    tools_k = _extract_tool_names(event_k)
    if not tools_j or not tools_k:
        return False
    # Same tool used again
    if set(tools_j) & set(tools_k):
        # And event_j had an error
        if _has_tool_error(event_j):
            return True
    return False


def _is_error_propagation(event_j: dict[str, Any], event_k: dict[str, Any]) -> bool:
    """Detect if event_k propagates error from event_j unchanged (PROPAGATE).

    PROPAGATE requires that event_k passes the error content onward (has its own
    tool calls or produces output that carries the error). If event_k is pure
    reasoning (no tools), the case is better classified as INVALIDATE.
    """
    if not _has_tool_error(event_j):
        return False
    # PROPAGATE only applies when downstream also has tool activity
    # (pure reasoning about an error is INVALIDATE, not PROPAGATE)
    tools_k = _extract_tool_names(event_k)
    if not tools_k:
        return False
    outputs_j = _extract_tool_outputs(event_j)
    content_k = _extract_content(event_k)
    # Check if error content from j appears in k without correction
    for output in outputs_j:
        if output and _content_overlap(output, content_k) > 0.3:
            # High overlap with error content, and k doesn't correct it
            if not _has_contradiction_signal(output, content_k):
                return True
    return False


def build_provenance_graph(packet: dict[str, Any]) -> ProvenanceGraph:
    """Build a typed provenance graph from a trajectory packet.

    Edge classification is entirely deterministic:
    - TRIGGER: sequential tool-call dependency (j calls tool, k uses result)
    - SUPPORT: j's output referenced positively in k
    - CONTRADICT: j's output contradicted by k
    - INVALIDATE: j's tool failure invalidates k's premise
    - OVERRIDE: k explicitly retries/corrects j's failed action
    - PROPAGATE: j's error passes unchanged into k
    - INDEPENDENT: no detectable relation (not stored as edge)
    """
    graph = ProvenanceGraph()
    events = packet.get("events") or []

    for event in events:
        graph.add_node(str(event["event_id"]))

    n = len(events)
    for i in range(n):
        event_i = events[i]
        eid_i = str(event_i["event_id"])
        tools_i = _extract_tool_names(event_i)
        outputs_i = _extract_tool_outputs(event_i)
        content_i = _extract_content(event_i)
        has_error_i = _has_tool_error(event_i)

        for j in range(i + 1, min(i + 5, n)):  # Window: look ahead up to 4 steps
            event_j = events[j]
            eid_j = str(event_j["event_id"])
            tools_j = _extract_tool_names(event_j)
            content_j = _extract_content(event_j)

            # Priority 1: OVERRIDE (k corrects j's failure)
            if _is_retry_with_correction(event_i, event_j):
                graph.add_edge(eid_i, eid_j, EdgeType.OVERRIDE,
                               evidence=f"retry_with_correction:{tools_i}")
                continue

            # Priority 2: PROPAGATE (error passes unchanged)
            if _is_error_propagation(event_i, event_j):
                graph.add_edge(eid_i, eid_j, EdgeType.PROPAGATE,
                               evidence=f"error_propagation:{tools_i}")
                continue

            # Priority 3: INVALIDATE (j's failure invalidates k's premise)
            if has_error_i and not tools_j:
                # event_i has tool error, event_j is reasoning that may depend on it
                overlap = _content_overlap(
                    " ".join(outputs_i), content_j
                )
                if overlap > 0.2:
                    graph.add_edge(eid_i, eid_j, EdgeType.INVALIDATE,
                                   evidence=f"tool_error_invalidates_premise:overlap={overlap:.2f}")
                    continue

            # Priority 4: TRIGGER (tool output used by next step)
            if tools_i and (content_j or tools_j):
                # Check if tool output from i is referenced in j
                for output in outputs_i:
                    if output and _content_overlap(output, content_j) > 0.15:
                        graph.add_edge(eid_i, eid_j, EdgeType.TRIGGER,
                                       evidence=f"tool_output_triggers_next:{tools_i}")
                        break
                else:
                    # Sequential tool calls with same tool family
                    if set(tools_i) & set(tools_j) and not has_error_i:
                        graph.add_edge(eid_i, eid_j, EdgeType.TRIGGER,
                                       evidence=f"sequential_same_tool:{tools_i}")
                continue

            # Priority 5: CONTRADICT (output contradicted)
            if content_i and content_j and _has_contradiction_signal(content_i, content_j):
                graph.add_edge(eid_i, eid_j, EdgeType.CONTRADICT,
                               evidence="contradiction_signal_detected")
                continue

            # Priority 6: SUPPORT (positive content reference)
            if content_i and content_j:
                overlap = _content_overlap(content_i, content_j)
                if overlap > 0.25:
                    graph.add_edge(eid_i, eid_j, EdgeType.SUPPORT,
                                   evidence=f"content_support:overlap={overlap:.2f}")
                    continue

            # No edge → INDEPENDENT (not stored)

    return graph


# ============================================================
# Phase 2: Causal Intervention Engine
# ============================================================

class CausalInterventionEngine:
    """Performs do-operations on the provenance graph.

    Core operation: do(step_i = ∅) removes all outgoing edges from step_i,
    then measures the interventional effect on downstream nodes.
    """

    def __init__(self, graph: ProvenanceGraph):
        self.graph = graph

    def do_intervention(self, target_node: str) -> dict[str, Any]:
        """Execute do(target_node = ∅): remove all outgoing edges.

        Returns:
            - affected_downstream: nodes that lose all SUPPORT/TRIGGER paths
            - total_downstream: all nodes reachable from target before intervention
            - interventional_effect: |affected| / |total|
            - severed_edges: edges removed
        """
        # Compute downstream before intervention
        total_downstream = self.graph.downstream_nodes(target_node)
        if not total_downstream:
            return {
                "target": target_node,
                "total_downstream": 0,
                "affected_downstream": 0,
                "interventional_effect": 0.0,
                "severed_edge_count": 0,
                "severed_edges": [],
            }

        severed_edges = list(self.graph.outgoing(target_node))

        # Compare reachability from all non-target ancestors before and after
        # removing the target's outgoing edges. This implements the documented
        # graph intervention instead of extrapolating from direct edges only.
        ancestors: set[str] = set()
        queue = deque([target_node])
        while queue:
            current = queue.popleft()
            for edge in self.graph.incoming(current):
                if edge.source not in ancestors:
                    ancestors.add(edge.source)
                    queue.append(edge.source)

        blocked = {(edge.source, edge.target, edge.edge_type) for edge in severed_edges}

        def reachable_from(starts: set[str], *, remove_target_edges: bool) -> set[str]:
            reached: set[str] = set(starts)
            pending = deque(starts)
            while pending:
                current = pending.popleft()
                for edge in self.graph.outgoing(current):
                    if remove_target_edges and (edge.source, edge.target, edge.edge_type) in blocked:
                        continue
                    if edge.target not in reached:
                        reached.add(edge.target)
                        pending.append(edge.target)
            return reached

        starts = ancestors or {target_node}
        before_reachable = reachable_from(starts, remove_target_edges=False)
        after_reachable = reachable_from(starts, remove_target_edges=True)
        affected_nodes = total_downstream & (before_reachable - after_reachable)

        effect = len(affected_nodes) / len(total_downstream) if total_downstream else 0.0

        return {
            "target": target_node,
            "total_downstream": len(total_downstream),
            "affected_downstream": len(affected_nodes),
            "interventional_effect": effect,
            "severed_edge_count": len(severed_edges),
            "severed_edges": [e.to_dict() for e in severed_edges],
        }

    def causal_backtrack(self, candidate_node: str) -> list[str]:
        """Backtrack along PROPAGATE edges to find the root cause.

        Solves the step off-by-1 problem: if a candidate is a PROPAGATE
        recipient, trace back through PROPAGATE edges to the original source.

        Returns:
            List of event_ids from candidate back to root cause (inclusive).
        """
        path = [candidate_node]
        current = candidate_node
        visited: set[str] = {candidate_node}

        while True:
            # Find incoming PROPAGATE edges
            propagate_sources = [
                e.source for e in self.graph.incoming(current)
                if e.edge_type == EdgeType.PROPAGATE and e.source not in visited
            ]
            if not propagate_sources:
                break
            # Follow the earliest source (closest to root)
            source = propagate_sources[0]
            path.append(source)
            visited.add(source)
            current = source

        # Path is [candidate, ..., root_cause]; reverse for root-first order
        path.reverse()
        return path

    def identify_root_cause(self, candidate_node: str) -> str:
        """Identify the root cause by backtracking PROPAGATE chains.

        Returns the earliest event in the propagation chain.
        """
        path = self.causal_backtrack(candidate_node)
        return path[0] if path else candidate_node


# ============================================================
# Phase 3: Evidential Scoring Module
# ============================================================

class EvidentialScore:
    """Dirichlet-based evidential score for a single candidate verification.

    The LLM outputs α_error and α_clean (Dirichlet concentration parameters).
    From these we derive:
    - expected_error: α_error / S (expected probability of error)
    - epistemic_uncertainty: 1 / (S + 1) (reducible: insufficient evidence)
    - aleatoric_uncertainty: p(1-p) * S/(S+1) (irreducible: inherent ambiguity)
    """

    __slots__ = ("alpha_error", "alpha_clean", "candidate_id", "event_id")

    def __init__(self, alpha_error: float, alpha_clean: float,
                 candidate_id: str = "", event_id: str = ""):
        # Enforce minimum alpha (Dirichlet prior: α ≥ 1 for proper distribution)
        self.alpha_error = max(float(alpha_error), 0.1)
        self.alpha_clean = max(float(alpha_clean), 0.1)
        self.candidate_id = candidate_id
        self.event_id = event_id

    @property
    def total_evidence(self) -> float:
        """S = α_error + α_clean (total evidence strength)."""
        return self.alpha_error + self.alpha_clean

    @property
    def expected_error(self) -> float:
        """E[p(error)] = α_error / S."""
        return self.alpha_error / self.total_evidence

    @property
    def epistemic_uncertainty(self) -> float:
        """Reducible uncertainty: 1 / (S + 1). High when evidence is scarce."""
        return 1.0 / (self.total_evidence + 1.0)

    @property
    def aleatoric_uncertainty(self) -> float:
        """Irreducible uncertainty: p(1-p) * S/(S+1). High when inherently ambiguous."""
        p = self.expected_error
        s = self.total_evidence
        return p * (1.0 - p) * s / (s + 1.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "event_id": self.event_id,
            "alpha_error": self.alpha_error,
            "alpha_clean": self.alpha_clean,
            "total_evidence": self.total_evidence,
            "expected_error": self.expected_error,
            "epistemic_uncertainty": self.epistemic_uncertainty,
            "aleatoric_uncertainty": self.aleatoric_uncertainty,
        }


def uncertainty_error_separation(
    score: EvidentialScore,
    tau_e: float = 0.40,
    tau_a: float = 0.20,
) -> str:
    """Apply uncertainty-error separation rule.

    Decision logic:
    - epistemic > τ_e → "unresolved" (insufficient evidence)
    - aleatoric > τ_a → "unresolved" (inherently ambiguous)
    - expected_error > 0.5 AND both uncertainties low → "error"
    - expected_error ≤ 0.5 AND both uncertainties low → "clean"

    Returns: "error" | "clean" | "unresolved"
    """
    if score.epistemic_uncertainty > tau_e:
        return "unresolved"
    if score.aleatoric_uncertainty > tau_a:
        return "unresolved"
    if score.expected_error > 0.5:
        return "error"
    return "clean"


# ============================================================
# Phase 4: Sequential Hypothesis Arbiter (E-value)
# ============================================================

class SequentialHypothesisArbiter:
    """Heuristic sequential evidence aggregation for trajectory verification.

    H0: trajectory is clean (no hallucination)
    H1: trajectory has hallucination (error)

    For each candidate verification, compute:
        e_t = P(data | H1) / P(data | H0)

    where P(data | H) is derived from the Dirichlet marginal likelihood.

    Cumulative: E_n = ∏ e_t

    Decision:
        E_n ≥ 1/α → heuristic error threshold
        E_n ≤ α   → accept H0 → verified_clean
        otherwise  → unresolved

    The LLM-produced concentrations are not calibrated e-values. This class
    therefore makes no frequentist FPR or optional-stopping guarantee.
    """

    def __init__(self, alpha: float = 0.10):
        """Initialize with a symmetric evidence threshold parameter.

        Args:
            alpha: threshold parameter in (0, 1).
        """
        if not (0 < alpha < 1):
            raise ValueError("alpha must be in (0, 1)")
        self.alpha = alpha
        self.rejection_threshold = 1.0 / alpha  # E_n ≥ 10 for α=0.10
        self.acceptance_threshold = alpha       # E_n ≤ 0.10 for α=0.10
        self._cumulative_log_e: float = 0.0
        self._step_count: int = 0
        self._history: list[dict[str, Any]] = []

    @property
    def cumulative_e_value(self) -> float:
        """Current cumulative e-value E_n = ∏ e_t."""
        return math.exp(self._cumulative_log_e)

    @property
    def log_cumulative_e_value(self) -> float:
        """Log of cumulative e-value (for numerical stability)."""
        return self._cumulative_log_e

    def compute_e_value(self, score: EvidentialScore) -> float:
        """Compute e-value for a single candidate from Dirichlet evidence.

        e_t = P(data | H1) / P(data | H0)

        Using the Dirichlet marginal likelihood:
        P(data | H1) ∝ α_error / (α_error + α_clean)  [error-favoring]
        P(data | H0) ∝ α_clean / (α_error + α_clean)   [clean-favoring]

        Therefore: e_t = α_error / α_clean

        This is the Bayes factor for a single observation under the
        two-hypothesis Dirichlet model.
        """
        # e_t = α_error / α_clean (Dirichlet Bayes factor)
        e_t = score.alpha_error / score.alpha_clean
        # Clamp for numerical stability
        return max(min(e_t, 100.0), 0.01)

    def update(self, score: EvidentialScore) -> dict[str, Any]:
        """Process one candidate's evidential score and update cumulative e-value.

        Returns step result with current decision state.
        """
        e_t = self.compute_e_value(score)
        log_e_t = math.log(e_t)
        self._cumulative_log_e += log_e_t
        self._step_count += 1

        decision = self._current_decision()
        step_result = {
            "step": self._step_count,
            "candidate_id": score.candidate_id,
            "e_t": e_t,
            "log_e_t": log_e_t,
            "cumulative_E_n": self.cumulative_e_value,
            "log_cumulative_E_n": self._cumulative_log_e,
            "decision": decision,
            "alpha": self.alpha,
            "rejection_threshold": self.rejection_threshold,
            "acceptance_threshold": self.acceptance_threshold,
        }
        self._history.append(step_result)
        return step_result

    def _current_decision(self) -> str:
        """Current heuristic decision using symmetric evidence thresholds."""
        e_n = self.cumulative_e_value
        # Use relative tolerance (1e-9) for boundary comparisons
        if e_n >= self.rejection_threshold * (1.0 - 1e-9):
            return "verified_error"
        elif e_n <= self.acceptance_threshold * (1.0 + 1e-9):
            return "verified_clean"
        return "unresolved"

    def final_decision(self) -> dict[str, Any]:
        """Return final decision after all candidates processed."""
        decision = self._current_decision()
        return {
            "state": decision,
            "cumulative_E_n": self.cumulative_e_value,
            "log_cumulative_E_n": self._cumulative_log_e,
            "step_count": self._step_count,
            "alpha": self.alpha,
            "rejection_threshold": self.rejection_threshold,
            "acceptance_threshold": self.acceptance_threshold,
            "fpr_guarantee": None,
            "calibration_status": "uncalibrated_heuristic",
            "history": self._history,
        }

    def reset(self) -> None:
        """Reset the arbiter for a new trajectory."""
        self._cumulative_log_e = 0.0
        self._step_count = 0
        self._history = []


# ============================================================
# Integration: CESV Pipeline (combines Phase 1-4)
# ============================================================

def run_cesv_pipeline(
    *,
    packet: dict[str, Any],
    candidates: list[dict[str, Any]],
    evidential_scores: list[EvidentialScore],
    alpha: float = 0.10,
    tau_e: float = 0.40,
    tau_a: float = 0.20,
) -> dict[str, Any]:
    """Execute the full CESV pipeline on one trajectory.

    Args:
        packet: OCSE packet (events with tool_interactions)
        candidates: Stage 1 candidate list (from dependency-aware scanning)
        evidential_scores: LLM-produced Dirichlet scores per candidate
        alpha: significance level for sequential test (FPR bound)
        tau_e: epistemic uncertainty threshold
        tau_a: aleatoric uncertainty threshold

    Returns:
        CESV verdict dict with state, responsible_event, diagnostics.
    """
    # Phase 1: Build provenance graph
    graph = build_provenance_graph(packet)

    # Phase 2: Causal intervention for each candidate
    engine = CausalInterventionEngine(graph)
    intervention_results: dict[str, dict[str, Any]] = {}
    root_causes: dict[str, str] = {}

    for candidate in candidates:
        origin = str(candidate.get("origin_hypothesis_event_id") or
                     candidate.get("origin_event_id") or "")
        if not origin:
            continue
        # Intervention
        intervention = engine.do_intervention(origin)
        intervention_results[origin] = intervention
        # Root cause backtracking
        root = engine.identify_root_cause(origin)
        root_causes[origin] = root
        if root not in intervention_results:
            intervention_results[root] = engine.do_intervention(root)

    # Phase 3: Uncertainty-error separation
    separation_results: list[dict[str, Any]] = []
    for score in evidential_scores:
        separation = uncertainty_error_separation(score, tau_e=tau_e, tau_a=tau_a)
        separation_results.append({
            **score.to_dict(),
            "separation_decision": separation,
        })

    # Phase 4: Sequential hypothesis testing
    # Only feed candidates that passed uncertainty separation (not unresolved)
    arbiter = SequentialHypothesisArbiter(alpha=alpha)
    decisive_scores = [
        score for score, result in zip(evidential_scores, separation_results)
        if result["separation_decision"] != "unresolved"
    ]

    # Absence of evidential scores is absence of evidence, not clean evidence.
    if not evidential_scores:
        return {
            "state": "unresolved",
            "responsible_event_id": None,
            "cesv_diagnostics": {
                "provenance_graph": graph.to_dict(),
                "intervention_results": intervention_results,
                "root_causes": root_causes,
                "separation_results": [],
                "sequential_test": arbiter.final_decision(),
                "step_results": [],
                "unresolved_count": 0,
                "total_candidates": 0,
                "decisive_candidates": 0,
                "alpha": alpha,
                "tau_e": tau_e,
                "tau_a": tau_a,
            },
        }

    # Sort by interventional effect (strongest causal impact first)
    decisive_scores.sort(
        key=lambda s: intervention_results.get(
            root_causes.get(s.event_id, s.event_id), {}
        ).get("interventional_effect", 0.0),
        reverse=True,
    )

    step_results = []
    for score in decisive_scores:
        step_result = arbiter.update(score)
        step_results.append(step_result)
        # Early stopping: if we've reached a decision, stop
        if step_result["decision"] != "unresolved":
            break

    final = arbiter.final_decision()

    # Determine responsible event
    responsible_event = None
    if final["state"] == "verified_error":
        # Find the candidate with highest interventional effect among error-evidence
        best_effect = -1.0
        for score in decisive_scores:
            root = root_causes.get(score.event_id, score.event_id)
            effect = intervention_results.get(root, {}).get("interventional_effect", 0.0)
            if score.expected_error > 0.5 and effect > best_effect:
                best_effect = effect
                responsible_event = root

    # Count unresolved from separation
    unresolved_count = sum(
        1 for r in separation_results if r["separation_decision"] == "unresolved"
    )
    total_candidates = len(evidential_scores)

    return {
        "state": final["state"],
        "responsible_event_id": responsible_event,
        "cesv_diagnostics": {
            "provenance_graph": graph.to_dict(),
            "intervention_results": intervention_results,
            "root_causes": root_causes,
            "separation_results": separation_results,
            "sequential_test": final,
            "step_results": step_results,
            "unresolved_count": unresolved_count,
            "total_candidates": total_candidates,
            "decisive_candidates": len(decisive_scores),
            "alpha": alpha,
            "tau_e": tau_e,
            "tau_a": tau_a,
        },
    }
