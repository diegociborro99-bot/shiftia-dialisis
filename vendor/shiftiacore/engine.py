"""
ShiftiaCoreV8 — el motor.

`solve(problem, config)` es el punto de entrada. Flujo:
  1. Expande el calendario (dow / finde / festivo).
  2. Crea las variables x[worker, día, turno] respetando turnos permitidos,
     preasignaciones (fijos) e indisponibilidades, y el estado de arrastre.
  3. Impone "exactamente un estado por persona y día".
  4. Recorre el registro de reglas (duras y/o blandas).
  5. Construye el objetivo: por defecto suma ponderada con TIERS lexicográficos
     garantizados; opcionalmente solves secuenciales (objective="lexicographic").
  6. Resuelve con CP-SAT.
  7. Si NO hay solución: explica el conflicto mínimo de reglas duras (IIS) y,
     si se pide, auto-relaja la regla dura menos prioritaria.
  8. Devuelve una Solution con planilla, violaciones, relajaciones, conflicto,
     métricas/KPIs y estadísticas.

`reoptimize(problem, baseline, ...)` re-optimiza minimizando los cambios frente
a una planilla ya publicada (mínima disrupción) usando además hints CP-SAT.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Optional

from ortools.sat.python import cp_model

from .models import (DayInfo, Problem, Relaxation, Rule, ShiftType, Solution,
                     Violation)
from .rules import BuildContext, RULES_REGISTRY, _sum
from .validate import validate_problem


@dataclass
class SolveConfig:
    time_limit_s: float = 10.0
    num_workers: int = 8
    seed: int = 0
    log: bool = False
    # reproducibilidad: con 1 hilo la planilla concreta es estable entre corridas
    # (con varios hilos el ÓPTIMO es el mismo, pero el cuadrante puede variar).
    deterministic: bool = False
    objective: str = "weighted"          # "weighted" | "lexicographic"
    explain_infeasible: bool = True
    relax_on_infeasible: bool = False
    baseline: Optional[dict] = None      # {worker_id: {day: code}} (re-optimización)
    stability_weight: int = 0            # peso de "no cambiar vs baseline"
    stability_tier: int = 0
    validate: bool = True                # valida la entrada antes de resolver
    # tope de coeficiente del objetivo ponderado; si se supera, el motor cambia
    # solo a lexicográfico (evita coeficientes astronómicos que ralentizan CP-SAT)
    weight_cap: int = 10 ** 15


# --------------------------------------------------------------------------- #
# Calendario
# --------------------------------------------------------------------------- #
def expand_calendar(problem: Problem) -> list[DayInfo]:
    H = problem.horizon_days
    provided = {d.index: d for d in problem.days}
    start = problem.meta.get("start_date")
    start_date = _dt.date.fromisoformat(start) if start else None

    holiday_dates, holiday_idx = set(), set()
    for h in problem.meta.get("holidays", []):
        (holiday_idx if isinstance(h, int) else holiday_dates).add(
            h if isinstance(h, int) else str(h))

    days = []
    for i in range(H):
        d = provided.get(i) or DayInfo(index=i)
        if start_date is not None:
            cur = start_date + _dt.timedelta(days=i)
            if d.date is None:
                d.date = cur.isoformat()
            if d.dow is None:
                d.dow = cur.weekday()
        if d.dow is not None and not d.is_weekend:
            d.is_weekend = d.dow >= 5
        if (i in holiday_idx) or (d.date in holiday_dates):
            d.is_holiday = True
        days.append(d)
    # asigna al problema para que problem.day(d) devuelva el día EXPANDIDO
    # (lo usan el auditor, las sustituciones, el stress-test, etc.).
    problem.days = days
    return days


def _shift_map(problem: Problem) -> dict[str, ShiftType]:
    m = {s.code: s for s in problem.shifts}
    if problem.rest_code not in m:
        m[problem.rest_code] = problem.rest_shift()
    for w in problem.workers:
        for code in list(w.fixed.values()) + list(w.history):
            if code not in m:
                m[code] = ShiftType(code=code, label=code, hours=0.0,
                                    is_work=False, is_rest=True)
    return m


def _build_variables(model, problem, smap):
    xvars, worksvars, cell_index = {}, {}, {}
    rest = problem.rest_code
    for w in problem.workers:
        allowed = problem.allowed_for(w)
        for d in range(problem.horizon_days):
            if d in w.fixed:
                cands = [w.fixed[d]]
            else:
                unavail = set(w.unavailable.get(d, []))
                cands = ([rest] if "*" in unavail
                         else [rest] + [c for c in allowed if c not in unavail])
            seen, cell_codes = set(), []
            for c in cands:
                if c not in seen:
                    seen.add(c)
                    cell_codes.append(c)
            cell = []
            for c in cell_codes:
                v = model.NewBoolVar(f"x:{w.id}:{d}:{c}")
                xvars[(w.id, d, c)] = v
                cell.append(v)
            model.AddExactlyOne(cell)
            cell_index[(w.id, d)] = cell_codes
            work_terms = [xvars[(w.id, d, c)] for c in cell_codes
                          if smap[c].is_work]
            wk = model.NewBoolVar(f"works:{w.id}:{d}")
            model.Add(wk == _sum(work_terms))
            worksvars[(w.id, d)] = wk
    return xvars, worksvars, cell_index


# --------------------------------------------------------------------------- #
# Construcción del modelo + reglas
# --------------------------------------------------------------------------- #
def _build(problem, smap, *, guarded, config: SolveConfig):
    model = cp_model.CpModel()
    xvars, worksvars, cell_index = _build_variables(model, problem, smap)
    ctx = BuildContext(model=model, problem=problem, smap=smap, xvars=xvars,
                       worksvars=worksvars, cell_codes=cell_index, guarded=guarded)
    ctx.unknown = []
    ctx.guard_rules = {}
    for rule in problem.rules:
        fn = RULES_REGISTRY.get(rule.type)
        if fn is None:
            ctx.unknown.append(rule.type)
            continue
        fn(ctx, rule)
        if guarded and rule.id in ctx.guards:
            ctx.guard_rules[rule.id] = rule

    # estabilidad (re-optimización con mínima disrupción)
    if config.baseline and config.stability_weight > 0:
        stab = Rule(type="stability", mode="soft",
                    weight=config.stability_weight, tier=config.stability_tier,
                    id="stability")
        for wid, days in config.baseline.items():
            for d, code in days.items():
                v = ctx.x(wid, int(d), code)
                if v is None:
                    continue
                miss = model.NewIntVar(0, 1, f"stab:{wid}:{d}")
                model.Add(miss == 1 - v)
                ctx.add_penalty(miss, config.stability_weight,
                                config.stability_tier, stab,
                                f"{wid} cambia día {d} vs publicado", 1)
                model.AddHint(v, 1)
    return model, ctx


def _tier_multipliers(penalties) -> dict[int, int]:
    tiers = sorted({p.tier for p in penalties})
    mult, cum = {}, 0
    for t in tiers:
        mult[t] = cum + 1
        cum += sum(p.base_weight * p.ub for p in penalties if p.tier == t) * mult[t]
    return mult


def _make_solver(config: SolveConfig) -> cp_model.CpSolver:
    s = cp_model.CpSolver()
    s.parameters.max_time_in_seconds = float(config.time_limit_s)
    s.parameters.num_search_workers = 1 if config.deterministic else int(config.num_workers)
    s.parameters.random_seed = int(config.seed)
    s.parameters.log_search_progress = bool(config.log)
    return s


def _run_lexicographic(problem, smap, config):
    """
    Optimización lexicográfica REAL: optimiza tier por tier (de mayor a menor),
    fijando el óptimo de cada tier antes de pasar al siguiente. Reconstruye el
    modelo por tier (robusto y sin depender de mutar el objetivo en sitio).
    Devuelve (status_code, solver, ctx, obj_on).
    """
    probe_model, probe_ctx = _build(problem, smap, guarded=False, config=config)
    tiers = sorted({p.tier for p in probe_ctx.penalties}, reverse=True)
    if not tiers:
        solver = _make_solver(config)
        st = solver.Solve(probe_model)
        probe_ctx.mult = {}
        return st, solver, probe_ctx, False
    locked, last = {}, (None, None)
    last_st = cp_model.UNKNOWN
    for k, t in enumerate(tiers):
        model, ctx = _build(problem, smap, guarded=False, config=config)
        ctx.mult = {tt: 1 for tt in tiers}
        for tj in tiers[:k]:
            terms = [p.base_weight * p.var for p in ctx.penalties if p.tier == tj]
            model.Add(_sum(terms) <= locked[tj])
        cur = [p.base_weight * p.var for p in ctx.penalties if p.tier == t]
        model.Minimize(_sum(cur))
        solver = _make_solver(config)
        st = solver.Solve(model)
        if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return st, solver, ctx, True
        locked[t] = int(round(solver.ObjectiveValue()))
        last, last_st = (solver, ctx), st
    return last_st, last[0], last[1], True


# --------------------------------------------------------------------------- #
# Métricas / KPIs
# --------------------------------------------------------------------------- #
def _weekend_pairs(problem):
    return [(d, d + 1) for d in range(problem.horizon_days - 1)
            if problem.day(d).dow == 5 and problem.day(d + 1).dow == 6]


def _metrics(problem, smap, schedule):
    H = problem.horizon_days
    night_codes = {s.code for s in problem.shifts if s.period == "night"}
    pairs = _weekend_pairs(problem)
    per = {}
    for w in problem.workers:
        row = schedule.get(w.id, {})
        codes = [row.get(d) for d in range(H)]
        hours = sum(smap[c].hours for c in codes if c and smap.get(c))
        work_days = sum(1 for c in codes if c and smap[c].is_work)
        nights = sum(1 for c in codes if c in night_codes)
        we_worked = sum(1 for (a, b) in pairs
                        if (codes[a] and smap[codes[a]].is_work) or
                           (codes[b] and smap[codes[b]].is_work))
        free_we = sum(1 for (a, b) in pairs
                      if not (codes[a] and smap[codes[a]].is_work) and
                         not (codes[b] and smap[codes[b]].is_work))
        per[w.id] = {"hours": round(hours, 2), "work_days": work_days,
                     "nights": nights, "weekends_worked": we_worked,
                     "free_weekends": free_we}

    def spread(key):
        vals = [v[key] for v in per.values()]
        return (max(vals) - min(vals)) if vals else 0

    # satisfacción de preferencias
    total_pref = sat_pref = 0
    for w in problem.workers:
        row = schedule.get(w.id, {})
        for pref in w.preferences:
            total_pref += 1
            assigned = row.get(pref.day)
            if pref.weight >= 0:
                sat_pref += 1 if assigned == pref.shift else 0
            else:
                sat_pref += 1 if assigned != pref.shift else 0
    pref_rate = round(sat_pref / total_pref, 3) if total_pref else None

    return {
        "per_worker": per,
        "fairness": {"nights_spread": spread("nights"),
                     "weekends_worked_spread": spread("weekends_worked"),
                     "hours_spread": round(spread("hours"), 2)},
        "preference_satisfaction": pref_rate,
        "total_hours": round(sum(p["hours"] for p in per.values()), 2),
    }


# --------------------------------------------------------------------------- #
# Extracción de la solución
# --------------------------------------------------------------------------- #
def _extract_schedule(problem, ctx, solver):
    """Extracción LINEAL usando el índice cell_codes (sin escanear todas las vars)."""
    sched = {}
    for w in problem.workers:
        row = {}
        for d in range(problem.horizon_days):
            for c in ctx.cell_codes.get((w.id, d), ()):
                if solver.Value(ctx.xvars[(w.id, d, c)]) == 1:
                    row[d] = c
                    break
        sched[w.id] = row
    return sched


def _aggregate_violations(ctx, solver, mult):
    agg = {}
    for p in ctx.penalties:
        amt = int(solver.Value(p.var))
        if amt <= 0:
            continue
        cost = amt * p.base_weight * mult.get(p.tier, 1)
        a = agg.setdefault(p.rule.id, {
            "rule_type": p.rule.type, "tier": p.tier, "weight": p.rule.weight,
            "amount": 0, "cost": 0, "labels": []})
        a["amount"] += amt
        a["cost"] += cost
        if len(a["labels"]) < 6:
            a["labels"].append(f"{p.label}×{amt}")
    out = []
    for rid, a in sorted(agg.items(), key=lambda kv: -kv[1]["cost"]):
        out.append(Violation(rule_id=rid, rule_type=a["rule_type"], tier=a["tier"],
                             amount=a["amount"], weight=a["weight"], cost=a["cost"],
                             detail="; ".join(a["labels"])))
    return out


def _stats(problem, ctx, solver, status, feasible, obj_on):
    return {
        "status": status,
        "objective": int(solver.ObjectiveValue()) if (feasible and obj_on) else (0 if feasible else None),
        "best_bound": round(solver.BestObjectiveBound(), 2) if obj_on else None,
        "wall_time_s": round(solver.WallTime(), 4),
        "conflicts": solver.NumConflicts(),
        "branches": solver.NumBranches(),
        "num_penalty_terms": len(ctx.penalties),
        "horizon_days": problem.horizon_days,
        "workers": len(problem.workers),
        "rules": len(problem.rules),
    }


# --------------------------------------------------------------------------- #
# Caminos de infeasibilidad
# --------------------------------------------------------------------------- #
def _explain_infeasible(problem, smap, config) -> list[str]:
    """Núcleo mínimo de reglas DURAS en conflicto (assumptions / IIS)."""
    model, ctx = _build(problem, smap, guarded=True, config=config)
    if not ctx.guards:
        return []
    model.AddAssumptions(list(ctx.guards.values()))
    solver = _make_solver(config)
    solver.parameters.num_search_workers = 1
    st = solver.Solve(model)
    if st != cp_model.INFEASIBLE:
        return []
    idx2rule = {g.Index(): rid for rid, g in ctx.guards.items()}
    core = solver.SufficientAssumptionsForInfeasibility()
    return sorted({idx2rule[i] for i in core if i in idx2rule})


def _solve_relaxed(problem, smap, config, conflict):
    """Auto-relaja la regla dura menos prioritaria y devuelve la mejor planilla."""
    model, ctx = _build(problem, smap, guarded=True, config=config)
    soft = list(ctx.penalties)
    mult = _tier_multipliers(soft)
    soft_max = sum(p.base_weight * mult[p.tier] * p.ub for p in soft) + 1
    obj = [p.base_weight * mult[p.tier] * p.var for p in soft]
    # penalización de relajar cada regla dura: domina al coste blando y crece
    # con el tier (se relaja antes lo de menor prioridad).
    for rid, g in ctx.guards.items():
        tier = ctx.guard_rules[rid].tier
        w = soft_max * (tier + 1)
        obj.append((1 - g) * w)
    model.Minimize(_sum(obj))
    solver = _make_solver(config)
    st = solver.Solve(model)
    status = solver.StatusName(st)
    feasible = st in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    if not feasible:
        return Solution(status=status, feasible=False, schedule={}, objective=0,
                        violations=[], stats=_stats(problem, ctx, solver, status, False, True),
                        justification=["No se pudo ni relajando."], conflict=conflict)
    relaxed = []
    for rid, g in ctx.guards.items():
        if solver.Value(g) == 0:
            r = ctx.guard_rules[rid]
            relaxed.append(Relaxation(rule_id=rid, rule_type=r.type, tier=r.tier,
                                      detail=f"Regla dura '{rid}' relajada para "
                                             f"poder generar una planilla."))
    sched = _extract_schedule(problem, ctx, solver)
    just = list(ctx.notes)
    just.insert(0, f"{len(problem.workers)} trabajadores · {problem.horizon_days} "
                   f"días · {len(problem.rules)} reglas (modo auto-relajación).")
    just.append(f"Se relajaron {len(relaxed)} regla(s) dura(s): "
                f"{', '.join(r.rule_id for r in relaxed) or 'ninguna'}.")
    return Solution(status="FEASIBLE_RELAXED", feasible=True, schedule=sched,
                    objective=int(solver.ObjectiveValue()),
                    violations=_aggregate_violations(ctx, solver, mult),
                    stats=_stats(problem, ctx, solver, status, True, True),
                    justification=just, metrics=_metrics(problem, smap, sched),
                    conflict=conflict, relaxations=relaxed)


# --------------------------------------------------------------------------- #
# Punto de entrada
# --------------------------------------------------------------------------- #
def solve(problem: Problem, config: Optional[SolveConfig] = None) -> Solution:
    config = config or SolveConfig()

    # ---- validación de entrada ----
    warnings = []
    if config.validate:
        rep = validate_problem(problem)
        warnings = rep.warnings
        if not rep.ok:
            return Solution(
                status="INVALID", feasible=False, schedule={}, objective=0,
                violations=[], stats={"status": "INVALID"},
                justification=["Entrada inválida; corrige los errores y reintenta."],
                errors=rep.errors, warnings=rep.warnings)

    problem.days = expand_calendar(problem)
    smap = _shift_map(problem)

    use_lex = config.objective == "lexicographic"
    lex_note = None
    if not use_lex:
        # guardarraíl: si el objetivo ponderado generaría coeficientes enormes,
        # cambiar solo a lexicográfico (más estable numéricamente).
        probe = _tier_multipliers(_build(problem, smap, guarded=False,
                                         config=config)[1].penalties)
        if probe and max(probe.values()) > config.weight_cap:
            use_lex = True
            lex_note = ("Objetivo ponderado con coeficientes demasiado grandes; "
                        "se usó optimización lexicográfica secuencial.")

    if use_lex:
        st, solver, ctx, obj_on = _run_lexicographic(problem, smap, config)
    else:
        model, ctx = _build(problem, smap, guarded=False, config=config)
        solver = _make_solver(config)
        mult = _tier_multipliers(ctx.penalties)
        ctx.mult = mult
        if ctx.penalties:
            model.Minimize(_sum([p.base_weight * mult[p.tier] * p.var
                                 for p in ctx.penalties]))
        st = solver.Solve(model)
        obj_on = bool(ctx.penalties)

    status = solver.StatusName(st)
    if lex_note:
        ctx.notes.append(lex_note)
    feasible = st in (cp_model.OPTIMAL, cp_model.FEASIBLE)

    if feasible:
        sched = _extract_schedule(problem, ctx, solver)
        viols = _aggregate_violations(ctx, solver, ctx.mult)
        just = list(ctx.notes)
        just.insert(0, f"{len(problem.workers)} trabajadores · "
                       f"{problem.horizon_days} días · {len(problem.rules)} reglas.")
        if ctx.unknown:
            just.append(f"Reglas desconocidas ignoradas: "
                        f"{', '.join(sorted(set(ctx.unknown)))}.")
        just.append(f"Estado {status}; "
                    + ("sin desvíos blandos." if not viols
                       else f"{len(viols)} regla(s) blanda(s) con desvío."))
        sol = Solution(status=status, feasible=True, schedule=sched,
                       objective=int(solver.ObjectiveValue()) if obj_on else 0,
                       violations=viols,
                       stats=_stats(problem, ctx, solver, status, True, obj_on),
                       justification=just,
                       metrics=_metrics(problem, smap, sched))
        sol.warnings = warnings
        return sol

    # ---- infeasible ----
    conflict = _explain_infeasible(problem, smap, config) \
        if config.explain_infeasible else []
    if config.relax_on_infeasible:
        sol = _solve_relaxed(problem, smap, config, conflict)
        sol.warnings = warnings
        return sol

    just = [f"{len(problem.workers)} trabajadores · {problem.horizon_days} días.",
            "No existe planilla que cumpla todas las reglas DURAS."]
    if conflict:
        just.append("Conflicto mínimo entre reglas duras: " + ", ".join(conflict)
                    + ". Relaja una de ellas (mode='soft') o revisa "
                      "cobertura/indisponibilidades.")
    else:
        just.append("Revisa cobertura, indisponibilidades o usa "
                    "relax_on_infeasible=True.")
    return Solution(status=status, feasible=False, schedule={}, objective=0,
                    violations=[], stats=_stats(problem, ctx, solver, status, False, obj_on),
                    justification=just, conflict=conflict, warnings=warnings)


def reoptimize(problem: Problem, baseline: dict,
               config: Optional[SolveConfig] = None,
               stability_weight: int = 20,
               stability_tier: int = 0) -> Solution:
    """
    Re-optimiza minimizando los cambios frente a `baseline` (planilla ya
    publicada). Ideal cuando entra una baja a mitad de mes: respeta lo nuevo y
    toca lo mínimo del resto. baseline = {worker_id: {day: code}}.
    """
    config = config or SolveConfig()
    config.baseline = baseline
    if config.stability_weight == 0:
        config.stability_weight = stability_weight
        config.stability_tier = stability_tier
    return solve(problem, config)
