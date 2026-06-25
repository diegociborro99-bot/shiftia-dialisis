"""
ShiftiaCoreV8 — alternativas multi-objetivo (frontera de decisiones).

En vez de una sola planilla, ofrece VARIAS distintas, cada una priorizando un
objetivo blando distinto (equidad, preferencias, findes libres, estabilidad…),
para que la gestora **elija el trade-off** viendo los KPIs de cada una.

Cómo: para cada "tema" blando presente, se clona el problema y se **promociona
ese tema a la cima del objetivo lexicográfico** (tier más alto entre los blandos)
+ se le sube el peso. Se resuelve, se deduplican planillas idénticas y se
devuelve cada alternativa con su perfil de métricas.

No cambia el motor: solo reordena prioridades blandas y vuelve a resolver. Las
reglas DURAS se respetan en todas (todas son válidas/conformes).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

from .engine import SolveConfig, solve

_LABELS = {
    "balance": "Máxima equidad",
    "preferences": "Máximas preferencias",
    "min_free_weekends": "Más findes libres",
    "max_weekends_worked": "Menos findes trabajados",
    "shift_stability": "Rotación más estable",
    "no_isolated_work": "Menos días sueltos",
    "no_isolated_rest": "Menos descansos sueltos",
    "contract_hours": "Horas más ajustadas al contrato",
    "no_mixed_weekends": "Findes no mixtos",
}


@dataclass
class Alternative:
    label: str
    emphasis: str | None
    feasible: bool
    objective: int
    schedule: dict
    metrics: dict = field(default_factory=dict)

    def to_dict(self):
        return {"label": self.label, "emphasis": self.emphasis,
                "feasible": self.feasible, "objective": self.objective,
                "schedule": self.schedule, "metrics": self.metrics}


def _profile(sol):
    m = sol.metrics or {}
    f = m.get("fairness", {})
    return {"objective": sol.objective,
            "nights_spread": f.get("nights_spread"),
            "weekends_spread": f.get("weekends_worked_spread"),
            "hours_spread": f.get("hours_spread"),
            "preference_satisfaction": m.get("preference_satisfaction"),
            "total_hours": m.get("total_hours"),
            "soft_violations": len(sol.violations)}


def _sig(sol):
    return tuple(sorted((w, tuple(sorted(d.items())))
                        for w, d in sol.schedule.items()))


def alternatives(problem, config: SolveConfig | None = None, *,
                 max_alternatives: int = 3, boost: int = 8) -> list[Alternative]:
    config = config or SolveConfig()
    base = solve(problem, config)
    out = [Alternative("Equilibrado", None, base.feasible, base.objective,
                       base.schedule, _profile(base))]
    if not base.feasible:
        return out

    soft_types, seen = [], set()
    for r in problem.rules:
        if r.mode.value == "soft" and r.weight and r.type not in seen:
            seen.add(r.type)
            soft_types.append(r.type)

    sigs = {_sig(base)}
    for t in soft_types:
        if len(out) >= max_alternatives:
            break
        clone = copy.deepcopy(problem)
        soft_tiers = [r.tier for r in clone.rules if r.mode.value == "soft"]
        top = (max(soft_tiers) if soft_tiers else 0) + 1
        for r in clone.rules:
            if r.type == t and r.mode.value == "soft":
                r.tier = top
                r.weight = int(r.weight) * boost
        sol = solve(clone, config)
        if not sol.feasible:
            continue
        s = _sig(sol)
        if s in sigs:
            continue       # idéntica a otra ya ofrecida
        sigs.add(s)
        out.append(Alternative(_LABELS.get(t, f"Prioriza {t}"), t, True,
                               sol.objective, sol.schedule, _profile(sol)))
    return out[:max_alternatives]
