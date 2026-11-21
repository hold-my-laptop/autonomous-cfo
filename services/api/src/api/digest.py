"""Vero Proof's weekly digest (docs/PROOF.md §6, §7.4)."""

from datetime import date, datetime

from api.decisions import subject_text
from api.domain.models import RecommendationStatus
from api.domain.proof.check import RoutineCheck
from api.domain.proof.findings import ProofFinding
from api.proof_schemas import Digest, DigestAction, DigestChange, DigestCounts
from api.state import DemoState


def by_period(finding: ProofFinding) -> tuple[date, datetime, str]:
    """By the date of what a finding concerns, oldest first."""
    return (
        finding.period_from or finding.created_at.date(),
        finding.created_at,
        str(finding.id),
    )


def latest_routine_checks(state: DemoState) -> list[RoutineCheck]:
    """The most recent statement payment of each fixed cost, in Cost Memory order."""
    latest: dict[str, RoutineCheck] = {}
    for check in state.routine_checks:
        current = latest.get(check.cost_ref)
        if current is None or (check.date, str(check.transaction_id)) > (
            current.date,
            str(current.transaction_id),
        ):
            latest[check.cost_ref] = check
    order = {item.cost_ref: i for i, item in enumerate(state.cost_memory)}
    return sorted(latest.values(), key=lambda c: (order.get(c.cost_ref, len(order)), c.cost_ref))


def _reason(finding: ProofFinding) -> str:
    labelled = [h for h in finding.history if h.classification is not None]
    return labelled[-1].label if labelled else finding.history[-1].label


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


def _actions(state: DemoState, finding: ProofFinding) -> list[DigestAction]:
    pending = (state.recommendations.get(i) for i in finding.recommendation_ids)
    return [
        DigestAction(
            recommendation_id=r.id,
            kind=r.kind,
            lane=r.lane,
            text=_sentence(subject_text(r)),
        )
        for r in pending
        if r is not None and r.status is RecommendationStatus.PENDING
    ]


def build_digest(state: DemoState) -> Digest:
    findings = sorted((f for f in state.findings.values() if f.in_digest), key=by_period)
    checks = latest_routine_checks(state)
    quiet = sum(f.quiet for f in findings)
    return Digest(
        as_of=state.as_of,
        counts=DigestCounts(
            changes=len(findings),
            handled_quietly=quiet,
            needs_owner=len(findings) - quiet,
            routine_checks=sum(c.matches for c in checks),
        ),
        changes=[
            DigestChange(
                finding_id=f.id,
                title=f.title,
                counterparty=f.counterparty,
                cost_ref=f.cost_ref,
                classification=f.classification,
                escalated=f.escalated,
                status=f.status,
                quiet=f.quiet,
                investigation_mode=f.investigation.mode if f.investigation else None,
                reason=_reason(f),
                actions=_actions(state, f),
            )
            for f in findings
        ],
        routine_checks=checks,
    )
