"""
ShiftiaCoreV8 — núcleo de optimización de turnos (el molde).

API pública:
    from shiftiacore import (
        Problem, Worker, ShiftType, DayInfo, Rule, RuleMode, Preference,
        Solution, Violation, solve, SolveConfig,
        register, available_rules,
    )

Uso mínimo:
    prob = Problem(horizon_days=7, shifts=[...], workers=[...], rules=[...])
    sol = solve(prob, SolveConfig(time_limit_s=10))
    print(sol.status, sol.schedule)
"""
from .models import (DayInfo, Preference, Problem, Relaxation, Rule, RuleMode,
                     ShiftType, Solution, Violation, Worker)
from .engine import SolveConfig, solve, reoptimize, expand_calendar
from .rules import available_rules, register, RULES_REGISTRY
from .validate import ValidationReport, validate_problem
from .demand import (DemandForecast, TimeDemandForecast, forecast_demand,
                     forecast_time_demand, synthesize_footfall,
                     synthesize_history)
from .nl_rules import (NLResult, build_prompt, interpret, parse_llm_json,
                       translate)
from .learn import (EditEpisode, Suggestion, apply_suggestions,
                    episode_from_sync, learn_from_edits)
from .compliance import (ComplianceReport, audit_compliance,
                         estatuto_marco_rules)
from .replace import (Candidate, can_release, can_swap, cover_absence,
                      cover_catastrophe, suggest_replacements)
from .pareto import Alternative, alternatives
from .robustness import FragilityReport, stress_test

__version__ = "8.15.0"

__all__ = [
    "Problem", "Worker", "ShiftType", "DayInfo", "Rule", "RuleMode",
    "Preference", "Solution", "Violation", "Relaxation",
    "solve", "reoptimize", "SolveConfig", "expand_calendar",
    "validate_problem", "ValidationReport",
    "forecast_demand", "DemandForecast", "synthesize_history",
    "forecast_time_demand", "TimeDemandForecast", "synthesize_footfall",
    "translate", "interpret", "build_prompt", "parse_llm_json", "NLResult",
    "learn_from_edits", "apply_suggestions", "EditEpisode", "Suggestion",
    "episode_from_sync",
    "audit_compliance", "ComplianceReport", "estatuto_marco_rules",
    "suggest_replacements", "cover_absence", "Candidate",
    "can_release", "can_swap", "cover_catastrophe",
    "alternatives", "Alternative",
    "stress_test", "FragilityReport",
    "register", "available_rules", "RULES_REGISTRY",
    "__version__",
]
