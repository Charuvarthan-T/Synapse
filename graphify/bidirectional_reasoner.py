"""Bidirectional LLM ↔ graph reasoning loop (Synapse contribution C2).

Flow:
  1. Retrieve graph context for the question (reuse weighted / community retrieval).
  2. Ask the LLM for an answer **and** structured graph-verifiable claims.
  3. Validate those claims against the knowledge graph.
  4. Optionally ask the LLM to revise using validation feedback.
  5. Emit a final answer that preserves per-claim evidence.

Graph → LLM context alone is not enough: this module adds the
LLM → graph validation direction and feeds results back into reasoning.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol

import networkx as nx

from graphify.claims import Claim, parse_claims, validate_claims
from graphify.graph_checks import AggregateReport, Verdict
from graphify.weights import RelationWeightRegistry


class ReasoningLLMProvider(Protocol):
    def complete(self, prompt: str, *, max_tokens: int) -> str: ...


DEFAULT_MAX_TOKENS = 1800
CLAIMS_SCHEMA_HINT = """
Return ONLY valid JSON with this shape:
{
  "answer": "your draft answer grounded in the graph context",
  "claims": [
    {"type": "exists", "symbol": "ClassOrFunctionName"},
    {"type": "calls", "source": "Caller", "target": "Callee"},
    {"type": "imports", "source": "module_a", "target": "module_b"},
    {"type": "inherits", "source": "Child", "target": "Parent"},
    {"type": "method_of", "source": "Owner", "target": "method_name"},
    {"type": "relation", "source": "A", "relation": "handles", "target": "B"}
  ]
}
Only include claims that the graph could in principle verify.
If the graph context is insufficient, say so in answer and use fewer claims.
""".strip()


@dataclass
class ReasoningResult:
    """Full bidirectional reasoning trace."""

    question: str
    graph_context: str
    draft_answer: str
    claims: list[Claim] = field(default_factory=list)
    validation: AggregateReport = field(default_factory=AggregateReport)
    revised_answer: str | None = None
    final_answer: str = ""
    parse_errors: list[str] = field(default_factory=list)
    provider_errors: list[str] = field(default_factory=list)
    revised: bool = False

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "draft_answer": self.draft_answer,
            "final_answer": self.final_answer,
            "revised": self.revised,
            "claims": [
                {
                    "type": c.type,
                    "source": c.source,
                    "target": c.target,
                    "relation": c.relation,
                    "symbol": c.symbol,
                    "describe": c.describe(),
                }
                for c in self.claims
            ],
            "validation": self.validation.to_dict(),
            "parse_errors": list(self.parse_errors),
            "provider_errors": list(self.provider_errors),
            "graph_context_chars": len(self.graph_context),
        }


def retrieve_graph_context(
    G: nx.Graph,
    question: str,
    *,
    depth: int = 3,
    token_budget: int = 2000,
    weighted: bool = True,
    community_aware: bool = True,
    registry: RelationWeightRegistry | None = None,
) -> str:
    """Reuse serve-side retrieval (weighted + optional community gate)."""
    from graphify.serve import _query_graph_text

    return _query_graph_text(
        G,
        question,
        depth=depth,
        token_budget=token_budget,
        weighted=weighted,
        community_aware=community_aware,
        registry=registry,
    )


def build_reason_prompt(question: str, graph_context: str) -> str:
    return (
        "You are reasoning over a repository knowledge graph.\n"
        "Use ONLY the graph context below. Do not invent symbols or edges.\n\n"
        f"QUESTION:\n{question}\n\n"
        f"GRAPH CONTEXT:\n{graph_context}\n\n"
        f"{CLAIMS_SCHEMA_HINT}\n"
    )


def build_revise_prompt(
    question: str,
    graph_context: str,
    draft_answer: str,
    validation: AggregateReport,
) -> str:
    report = json.dumps(validation.to_dict(), indent=2, ensure_ascii=False)
    return (
        "Revise your answer using graph validation feedback.\n"
        "Rules:\n"
        "- Treat CONTRADICTED claims as false; remove or correct them.\n"
        "- Treat UNKNOWN claims as unverified; do not assert them as facts.\n"
        "- Prefer SUPPORTED claims and cite the symbols involved.\n"
        "- If too much is UNKNOWN/CONTRADICTED, say the graph cannot confirm the answer.\n"
        "Return ONLY JSON: {\"answer\": \"...\", \"claims\": [] } "
        "(claims may be empty on revision).\n\n"
        f"QUESTION:\n{question}\n\n"
        f"GRAPH CONTEXT:\n{graph_context}\n\n"
        f"DRAFT ANSWER:\n{draft_answer}\n\n"
        f"VALIDATION REPORT:\n{report}\n"
    )


def _extract_answer_and_claims(raw: str) -> tuple[str, list[Claim], list[str]]:
    errors: list[str] = []
    text = (raw or "").strip()
    if not text:
        return "", [], ["empty model response"]

    claims, claim_errors = parse_claims(text)
    errors.extend(claim_errors)

    answer = ""
    try:
        from graphify.claims import extract_json_blob

        data = extract_json_blob(text)
        if isinstance(data, dict) and isinstance(data.get("answer"), str):
            answer = data["answer"].strip()
            if not claims and isinstance(data.get("claims"), list):
                claims, more_err = parse_claims(json.dumps({"claims": data["claims"]}))
                errors.extend(more_err)
    except Exception as exc:  # noqa: BLE001 — fail soft
        errors.append(f"answer extract failed: {exc}")

    if not answer:
        answer = text
    return answer, claims, errors


def _needs_revision(report: AggregateReport) -> bool:
    if not report.results:
        return False
    return any(
        r.verdict in (Verdict.CONTRADICTED, Verdict.UNKNOWN) for r in report.results
    ) or report.verdict is Verdict.CONTRADICTED


def format_final_answer(result: ReasoningResult) -> str:
    """Human-readable answer with an evidence appendix."""
    body = (result.revised_answer or result.draft_answer or "").strip()
    lines = [body, "", "---", "Graph validation:"]
    if not result.validation.results:
        lines.append("- (no structured claims to validate)")
    else:
        lines.append(
            f"- overall={result.validation.verdict.value} "
            f"(supported={result.validation.to_dict()['counts']['supported']}, "
            f"contradicted={result.validation.to_dict()['counts']['contradicted']}, "
            f"unknown={result.validation.to_dict()['counts']['unknown']})"
        )
        for item in result.validation.results:
            lines.append(f"- [{item.verdict.value}] {item.claim}: {item.reason}")
            for ev in item.evidence:
                lines.append(f"    evidence: {ev.detail}")
    if result.parse_errors:
        lines.append("- parse notes: " + "; ".join(result.parse_errors[:3]))
    return "\n".join(lines).rstrip() + "\n"


def reason(
    G: nx.Graph,
    question: str,
    *,
    provider: ReasoningLLMProvider | None = None,
    revise: bool = True,
    depth: int = 3,
    token_budget: int = 2000,
    weighted: bool = True,
    community_aware: bool = True,
    registry: RelationWeightRegistry | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    graph_context: str | None = None,
) -> ReasoningResult:
    """Run the bidirectional reasoning loop.

    When ``provider`` is ``None``, retrieval still runs and the result explains
    that no LLM is configured — fail-safe, no fabricated claims.
    """
    context = graph_context if graph_context is not None else retrieve_graph_context(
        G,
        question,
        depth=depth,
        token_budget=token_budget,
        weighted=weighted,
        community_aware=community_aware,
        registry=registry,
    )

    result = ReasoningResult(question=question, graph_context=context, draft_answer="")

    if provider is None:
        from graphify.semantic_extraction import default_provider

        provider = default_provider()

    if provider is None:
        result.provider_errors.append("no LLM provider configured")
        result.draft_answer = (
            "Cannot run bidirectional reasoning without an LLM provider. "
            "Graph context was retrieved successfully; configure a backend to continue."
        )
        result.final_answer = format_final_answer(result)
        return result

    try:
        raw = provider.complete(
            build_reason_prompt(question, context), max_tokens=max_tokens
        )
    except Exception as exc:  # noqa: BLE001
        result.provider_errors.append(str(exc))
        result.draft_answer = "LLM provider failed during initial reasoning."
        result.final_answer = format_final_answer(result)
        return result

    draft, claims, errors = _extract_answer_and_claims(raw)
    result.draft_answer = draft
    result.claims = claims
    result.parse_errors.extend(errors)
    result.validation = validate_claims(G, claims)

    if revise and _needs_revision(result.validation):
        try:
            raw2 = provider.complete(
                build_revise_prompt(question, context, draft, result.validation),
                max_tokens=max_tokens,
            )
            revised, claims2, errors2 = _extract_answer_and_claims(raw2)
            result.revised_answer = revised
            result.revised = True
            result.parse_errors.extend(errors2)
            # Re-validate any claims retained after revision.
            if claims2:
                result.claims = claims2
                result.validation = validate_claims(G, claims2)
        except Exception as exc:  # noqa: BLE001
            result.provider_errors.append(f"revise failed: {exc}")

    # Safety: if overall contradicted and no successful revision, prefer a cautionary final.
    if (
        result.validation.verdict is Verdict.CONTRADICTED
        and not result.revised_answer
    ):
        result.final_answer = format_final_answer(
            ReasoningResult(
                question=question,
                graph_context=context,
                draft_answer=(
                    "Graph validation contradicted one or more key claims. "
                    "Refusing to present the draft as factual.\n\n"
                    f"Draft was:\n{result.draft_answer}"
                ),
                claims=result.claims,
                validation=result.validation,
                parse_errors=result.parse_errors,
                provider_errors=result.provider_errors,
            )
        )
    else:
        result.final_answer = format_final_answer(result)
    return result
