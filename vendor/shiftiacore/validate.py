"""
ShiftiaCoreV8 — validación de entrada.

`validate_problem(problem)` revisa el problema ANTES de resolver y devuelve un
informe con:
  - errors:   problemas bloqueantes (datos incoherentes; el solve no debería
              correr o dará basura/fallos oscuros).
  - warnings: problemas probables pero no bloqueantes (p.ej. demanda que supera
              la plantilla → casi seguro infeasible, pero si la cobertura es
              blanda puede tener sentido).

Objetivo: que un cliente mal configurado reciba un mensaje claro en vez de un
INFEASIBLE opaco o un fallo del solver. Es la primera línea de un molde
multi-cliente.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import Problem
from .rules import RULES_REGISTRY


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _expanded_days(problem: Problem):
    # importación diferida para evitar ciclo engine<->validate
    from .engine import expand_calendar
    return expand_calendar(problem)


def validate_problem(problem: Problem) -> ValidationReport:
    rep = ValidationReport()
    E, W = rep.errors.append, rep.warnings.append

    H = problem.horizon_days
    if H is None or H <= 0:
        E(f"horizon_days debe ser > 0 (es {H}).")
        return rep  # sin horizonte no tiene sentido seguir

    # ---- turnos ----
    codes = [s.code for s in problem.shifts]
    dup = {c for c in codes if codes.count(c) > 1}
    if dup:
        E(f"Códigos de turno duplicados: {sorted(dup)}.")
    code_set = set(codes)
    if not any(s.is_work for s in problem.shifts):
        E("No hay ningún turno de trabajo (is_work=True).")
    for s in problem.shifts:
        if s.hours is not None and s.hours < 0:
            E(f"Turno '{s.code}': hours negativas ({s.hours}).")
        for fld in ("start", "end"):
            val = getattr(s, fld)
            if val is not None:
                try:
                    h, m = str(val).split(":")
                    if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
                        raise ValueError
                except Exception:
                    E(f"Turno '{s.code}': {fld}='{val}' no es 'HH:MM' válido.")
    if problem.rest_code not in code_set:
        W(f"rest_code '{problem.rest_code}' no está definido como turno; se "
          f"usará un descanso implícito.")

    work_codes = {s.code for s in problem.shifts if s.is_work}

    # ---- trabajadores ----
    ids = [w.id for w in problem.workers]
    dupw = {i for i in ids if ids.count(i) > 1}
    if dupw:
        E(f"IDs de trabajador duplicados: {sorted(dupw)}.")
    if not problem.workers:
        E("No hay trabajadores.")

    def in_range(d):
        return isinstance(d, int) and 0 <= d < H

    for w in problem.workers:
        if w.allowed_shifts is not None:
            for c in w.allowed_shifts:
                if c not in code_set:
                    E(f"{w.id}: allowed_shifts referencia turno inexistente '{c}'.")
                elif c not in work_codes:
                    W(f"{w.id}: allowed_shifts incluye '{c}' que no es de trabajo.")
            if not (set(w.allowed_shifts) & work_codes):
                W(f"{w.id}: sin turnos de trabajo permitidos; siempre descansará.")
        for d, c in w.fixed.items():
            if not in_range(int(d)):
                E(f"{w.id}: fixed día {d} fuera de rango (0..{H-1}).")
            if c not in code_set:
                W(f"{w.id}: fixed día {d}='{c}' no es un turno definido; se "
                  f"tratará como ausencia (no-trabajo).")
        for d, lst in w.unavailable.items():
            if not in_range(int(d)):
                E(f"{w.id}: unavailable día {d} fuera de rango.")
            for c in lst:
                if c != "*" and c not in code_set:
                    W(f"{w.id}: unavailable día {d} referencia turno '{c}' "
                      f"inexistente.")
            if int(d) in {int(k) for k in w.fixed}:
                W(f"{w.id}: día {d} aparece en fixed y unavailable; gana fixed.")
        for p in w.preferences:
            if not in_range(p.day):
                E(f"{w.id}: preferencia día {p.day} fuera de rango.")
            if p.shift not in code_set:
                E(f"{w.id}: preferencia referencia turno inexistente '{p.shift}'.")
        if len(w.history) > H + 60:
            W(f"{w.id}: history muy largo ({len(w.history)}); solo importan los "
              f"últimos días para las rachas.")
        for c in w.history:
            if c not in code_set:
                W(f"{w.id}: history incluye '{c}' no definido; cuenta como "
                  f"no-trabajo.")
        if w.contract_hours is not None:
            max_h = max((s.hours for s in problem.shifts if s.is_work), default=0)
            if w.contract_hours > H * max_h:
                W(f"{w.id}: contract_hours={w.contract_hours} supera el máximo "
                  f"posible ({H*max_h}) en el horizonte.")

    # ---- reglas ----
    rule_ids = [r.id for r in problem.rules]
    dupr = {i for i in rule_ids if rule_ids.count(i) > 1}
    if dupr:
        W(f"IDs de regla duplicados: {sorted(dupr)} (dificulta leer el reporte).")
    id_set = {w.id for w in problem.workers}
    grp_set = {g for w in problem.workers for g in w.groups}
    skl_set = {s for w in problem.workers for s in w.skills}
    for r in problem.rules:
        if r.type not in RULES_REGISTRY:
            W(f"Regla '{r.type}' (id={r.id}) no existe; el motor la ignorará. "
              f"Disponibles: {', '.join(sorted(RULES_REGISTRY))}.")
            continue
        if r.weight is not None and r.weight < 0:
            E(f"Regla '{r.id}': weight negativo ({r.weight}).")
        sc = r.scope or {}
        for k, universe, name in (("workers", id_set, "trabajador"),
                                  ("groups", grp_set, "grupo"),
                                  ("skills", skl_set, "skill")):
            for v in sc.get(k, []):
                if v not in universe:
                    W(f"Regla '{r.id}': scope.{k} '{v}' no coincide con ningún "
                      f"{name}.")
        # turnos referenciados en params
        refs = []
        p = r.params or {}
        for key in ("shifts", "from", "to"):
            v = p.get(key)
            if isinstance(v, str):
                refs.append(v)
            elif isinstance(v, list):
                refs.extend(v)
        if r.type == "coverage":
            for dem in [p.get("demand", {}), *p.get("by_daytype", {}).values(),
                        *p.get("by_day", {}).values()]:
                refs.extend(dem.keys())
        for c in refs:
            if c not in code_set:
                E(f"Regla '{r.id}' ({r.type}) referencia turno inexistente '{c}'.")

    # ---- feasibilidad de cobertura (heurística) ----
    days = _expanded_days(problem)

    def kind(day):
        return "holiday" if day.is_holiday else ("weekend" if day.is_weekend
                                                 else "weekday")

    for r in problem.rules:
        if r.type != "coverage" or r.mode.value != "hard":
            continue
        p = r.params or {}
        for d in range(H):
            day = days[d]
            dem = (p.get("by_day", {}).get(d) or p.get("by_day", {}).get(str(d))
                   or p.get("by_daytype", {}).get(kind(day)) or p.get("demand", {}))
            if not dem:
                continue
            req = 0
            for c, spec in dem.items():
                lo = spec if isinstance(spec, int) else (spec or {}).get("min", 0)
                req += lo or 0
                # ¿hay alguien que pueda hacer ese turno ese día?
                if (lo or 0) > 0:
                    elig = [w for w in problem.workers
                            if c in problem.allowed_for(w)
                            and "*" not in w.unavailable.get(d, [])
                            and c not in w.unavailable.get(d, [])
                            and (d not in w.fixed or w.fixed[d] == c)]
                    if not elig:
                        W(f"Cobertura dura pide '{c}' el día {d} pero nadie puede "
                          f"hacerlo (allowed/indisponibilidad). Infeasible.")
            # capacidad: nº de personas disponibles ese día
            cap = sum(1 for w in problem.workers
                      if "*" not in w.unavailable.get(d, [])
                      and (d not in w.fixed or problem.shift(w.fixed[d]) and
                           problem.shift(w.fixed[d]).is_work))
            if req > cap:
                W(f"Día {d}: la cobertura mínima dura suma {req} pero solo hay "
                  f"~{cap} personas disponibles. Casi seguro infeasible.")

    return rep
