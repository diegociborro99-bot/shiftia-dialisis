"""
ShiftiaCoreV8 — robustez ante bajas.

¿Cómo de frágil es un cuadrante si alguien causa baja? `stress_test` simula la
ausencia de cada persona y reporta:
  - puntos FRÁGILES: turnos cubiertos justo al mínimo (perder a cualquiera rompe
    la cobertura),
  - criticidad por persona (cuántos turnos dependen de ella) y por día,
  - % de resiliencia (cuántos turnos cubiertos aguantarían una baja).

Para CONSTRUIR cuadrantes robustos, la regla `coverage` admite `buffer`: exige
`min + buffer` (sobredimensiona para absorber bajas). Determinista y explicable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .models import Problem


@dataclass
class FragilityReport:
    robust: bool
    resilience: float                 # 0..1 (turnos cubiertos que aguantan 1 baja)
    fragile_points: list[dict] = field(default_factory=list)   # [{day, shift, count, need}]
    by_worker: dict = field(default_factory=dict)              # worker -> nº turnos críticos
    by_day: dict = field(default_factory=dict)
    summary: dict = field(default_factory=dict)

    def to_dict(self):
        return {"robust": self.robust, "resilience": round(self.resilience, 3),
                "fragile_points": self.fragile_points, "by_worker": self.by_worker,
                "by_day": self.by_day, "summary": self.summary}


def stress_test(problem: Problem, schedule: dict,
                rules: Optional[list] = None) -> FragilityReport:
    rules = rules if rules is not None else problem.rules
    from .engine import expand_calendar, _shift_map
    expand_calendar(problem)
    smap = _shift_map(problem)
    covs = [r for r in rules if r.type == "coverage" and r.mode.value == "hard"]

    def demand_for(d):
        out = {}
        for r in covs:
            p = r.params
            day = problem.day(d)
            kind = "holiday" if day.is_holiday else ("weekend" if day.is_weekend else "weekday")
            dem = ({int(k): v for k, v in p.get("by_day", {}).items()}.get(d)
                   or p.get("by_daytype", {}).get(kind) or p.get("demand", {}))
            for code, spec in dem.items():
                lo = spec if isinstance(spec, int) else spec.get("min")
                if lo:
                    out[code] = max(out.get(code, 0), lo)
        return out

    def code_at(wid, d):
        return (schedule.get(wid, {}) or {}).get(d, problem.rest_code)

    fragile, by_worker, by_day = [], {}, {}
    covered_total = 0
    for d in range(problem.horizon_days):
        dem = demand_for(d)
        for shift, need in dem.items():
            if need <= 0:
                continue
            covered_total += 1
            on = [w.id for w in problem.workers if code_at(w.id, d) == shift]
            cnt = len(on)
            if cnt <= need:        # al límite o ya corto → perder a uno rompe
                fragile.append({"day": d, "shift": shift, "count": cnt, "need": need})
                by_day[d] = by_day.get(d, 0) + 1
                for wid in on:
                    by_worker[wid] = by_worker.get(wid, 0) + 1

    resilience = 1.0 if covered_total == 0 else 1 - len(fragile) / covered_total
    # criticidad ordenada (quién es más insustituible)
    by_worker = dict(sorted(by_worker.items(), key=lambda kv: -kv[1]))
    return FragilityReport(
        robust=len(fragile) == 0, resilience=resilience,
        fragile_points=fragile, by_worker=by_worker, by_day=by_day,
        summary={"covered_shifts": covered_total, "fragile_shifts": len(fragile),
                 "most_critical": next(iter(by_worker), None)})
