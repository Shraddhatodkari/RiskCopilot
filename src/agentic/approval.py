"""
Human-approval state machine for a draft risk memo — the hard gate
required by ADR-006 and the project's business case ("Any LLM-generated
qualitative narrative requires the critic step to pass AND a human
reviewer to approve before the memo is finalized"). This module enforces
that as actual code, not just as a documented intention: there is no
function anywhere in this codebase that marks a memo APPROVED without
going through `approve()`, and `approve()` refuses a memo whose critic
report failed unless a human explicitly overrides it with a written
reason (logged, never silent).
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel

from src.agentic.critic import CriticReport


class MemoStatus(str, Enum):
    DRAFT = "draft"
    CRITIC_FAILED = "critic_failed"
    PENDING_HUMAN_APPROVAL = "pending_human_approval"
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalError(RuntimeError):
    pass


class RiskMemo(BaseModel):
    cik: str
    entity_name: str
    fiscal_year: int
    narrative_text: str
    critic_report: CriticReport
    status: MemoStatus
    override_reason: str | None = None
    reviewed_by: str | None = None
    reviewed_at: str | None = None


def create_memo(
    cik: str, entity_name: str, fiscal_year: int, narrative_text: str, critic_report: CriticReport
) -> RiskMemo:
    status = MemoStatus.PENDING_HUMAN_APPROVAL if critic_report.passed else MemoStatus.CRITIC_FAILED
    return RiskMemo(
        cik=cik,
        entity_name=entity_name,
        fiscal_year=fiscal_year,
        narrative_text=narrative_text,
        critic_report=critic_report,
        status=status,
    )


def approve(memo: RiskMemo, reviewer: str, override_reason: str | None = None) -> RiskMemo:
    if memo.status == MemoStatus.APPROVED:
        raise ApprovalError("memo is already approved")
    if memo.status == MemoStatus.REJECTED:
        raise ApprovalError("cannot approve a memo that was already rejected")
    if memo.status == MemoStatus.CRITIC_FAILED and not override_reason:
        raise ApprovalError(
            "this memo failed the automated grounding critic "
            f"(ungrounded_citations={memo.critic_report.ungrounded_citations}, "
            f"uncited_numeric_sentences={memo.critic_report.uncited_numeric_sentences}); "
            "approving it anyway requires an explicit, recorded override_reason "
            "so there is an audit trail for why a human chose to override an "
            "automated safety check, per this project's human-in-the-loop design."
        )
    if not reviewer:
        raise ApprovalError("reviewer identity is required to approve a memo")

    return memo.model_copy(
        update={
            "status": MemoStatus.APPROVED,
            "override_reason": override_reason,
            "reviewed_by": reviewer,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        }
    )


def reject(memo: RiskMemo, reviewer: str, reason: str) -> RiskMemo:
    if memo.status == MemoStatus.APPROVED:
        raise ApprovalError("cannot reject a memo that was already approved")
    if not reason:
        raise ApprovalError("a reason is required to reject a memo")
    return memo.model_copy(
        update={
            "status": MemoStatus.REJECTED,
            "override_reason": reason,
            "reviewed_by": reviewer,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        }
    )
