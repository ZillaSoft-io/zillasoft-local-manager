"""Per-session cost report from a UsageTracker, and persistence helpers."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from ..agents.usage import UsageTracker
from .pricing import cost_for

logger = logging.getLogger(__name__)

# Agent label -> phase bucket (spec §4.2 "by_phase"). Approximate: Sonnet also
# writes instructions, but its dominant cost is review/testing.
_PHASE_MAP = {
    "haiku": "input_parsing",
    "opus": "code_generation",
    "sonnet": "testing",
}


@dataclass
class CostReport:
    total: float = 0.0
    by_agent: dict[str, float] = field(default_factory=dict)
    by_model: dict[str, float] = field(default_factory=dict)
    by_phase: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_tracker(cls, tracker: UsageTracker) -> "CostReport":
        by_model = {m: cost_for(m, u) for m, u in tracker.by_model.items()}
        by_agent = {a: cost_for(a, u) for a, u in tracker.by_agent.items()}
        by_phase: dict[str, float] = {}
        for agent, c in by_agent.items():
            phase = _PHASE_MAP.get(agent, "other")
            by_phase[phase] = round(by_phase.get(phase, 0.0) + c, 6)
        total = round(sum(by_model.values()), 6)
        return cls(total=total, by_agent=by_agent, by_model=by_model,
                   by_phase=by_phase)

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "by_agent": self.by_agent,
            "by_model": self.by_model,
            "by_phase": self.by_phase,
        }


def record_session_cost(db, audit, session_id: str, project: Optional[str],
                        tracker: UsageTracker, budget=None) -> CostReport:
    """Compute the session cost, persist it to the DB + audit, and (if a
    budget is given) add it to the monthly spend. Returns the report."""
    report = CostReport.from_tracker(tracker)
    total_tokens = tracker.total.total_input + tracker.total.output_tokens
    db.update_session(session_id, total_cost=report.total,
                      total_tokens_used=total_tokens,
                      cost_breakdown=report.as_dict())
    audit.update(session_id, project, {"cost_summary": report.as_dict()})
    if budget is not None:
        budget.record_spend(report.total)
    return report
