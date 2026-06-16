"""Phase 4 — cost: pricing, report, monthly budget, persistence."""
from __future__ import annotations

from app.agents.usage import Usage, UsageTracker
from app.audit import AuditTrail
from app.cost import CostReport, MonthlyBudget, cost_for, record_session_cost
from app.database import Database


# --------------------------- pricing --------------------------- #
def test_cost_for_input_output():
    assert cost_for("claude-opus-4-8", Usage(input_tokens=1_000_000)) == 5.0
    assert cost_for("claude-haiku-4-5", Usage(input_tokens=1_000_000)) == 1.0
    assert cost_for("claude-sonnet-4-6", Usage(output_tokens=1_000_000)) == 15.0


def test_cost_for_cache_multipliers():
    assert cost_for("claude-opus-4-8",
                    Usage(cache_read_input_tokens=1_000_000)) == 0.5     # 0.10x
    assert cost_for("claude-opus-4-8",
                    Usage(cache_creation_input_tokens=1_000_000)) == 6.25  # 1.25x


# --------------------------- report --------------------------- #
def test_report_totals_and_phases():
    t = UsageTracker()
    t.record("haiku", "claude-haiku-4-5", Usage(input_tokens=1_000_000))   # $1
    t.record("opus", "claude-opus-4-8", Usage(output_tokens=1_000_000))     # $25
    t.record("sonnet", "claude-sonnet-4-6", Usage(input_tokens=1_000_000))  # $3
    r = CostReport.from_tracker(t)
    assert r.total == 29.0
    assert r.by_phase["input_parsing"] == 1.0
    assert r.by_phase["code_generation"] == 25.0
    assert r.by_phase["testing"] == 3.0


def test_record_session_cost_persists(config, tmp_path):
    db = Database(tmp_path / "c.db")
    audit = AuditTrail(tmp_path / "audit")
    sid = db.create_session(task_type="bug_fix", project="website")
    t = UsageTracker()
    t.record("opus", "claude-opus-4-8", Usage(output_tokens=1_000_000))  # $25
    budget = MonthlyBudget(config)
    before = budget.spent
    report = record_session_cost(db, audit, sid, "website", t, budget)
    assert report.total == 25.0
    session = db.get_session(sid)
    assert session["total_cost"] == 25.0
    assert session["cost_breakdown"]["total"] == 25.0
    assert budget.spent == round(before + 25.0, 4)
    assert audit.read(sid, "website")["cost_summary"]["total"] == 25.0


# --------------------------- budget --------------------------- #
def test_budget_would_exceed(config):
    b = MonthlyBudget(config)  # cap 100, spent 0
    assert b.would_exceed(150) is True
    assert b.would_exceed(150, scope_level="uncapped") is False
    assert b.would_exceed(50) is False


def test_budget_thresholds(config):
    b = MonthlyBudget(config)  # cap 100
    assert b.thresholds_crossed(40, 60) == [0.5]
    assert b.thresholds_crossed(0, 100) == [0.5, 0.8, 1.0]
    assert b.thresholds_crossed(85, 90) == []


def test_budget_record_spend_persists(config):
    b = MonthlyBudget(config)
    b.record_spend(10.0)
    assert b.spent == 10.0
    assert config.get("LOCAL_MANAGER_CURRENT_MONTH_SPENT") == 10.0


def test_budget_auto_reset(config):
    config.set("LOCAL_MANAGER_COST_RESET_MONTH", "2020-01", actor="agent")
    config.set("LOCAL_MANAGER_CURRENT_MONTH_SPENT", 42.0, actor="agent")
    b = MonthlyBudget(config)
    assert b.maybe_reset() is True
    assert b.spent == 0.0
    assert b.maybe_reset() is False   # already reset to current month
