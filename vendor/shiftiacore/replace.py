"""
ShiftiaCoreV8 — autopiloto de sustituciones (Pilar 3).

La baja a las 6am es el momento de pánico. Este módulo, dado el cuadrante
publicado y un hueco (un turno que cubrir), devuelve los **mejores sustitutos
rankeados**, cada uno con su "por qué":
  - LEGAL: asignarlo no rompe ninguna regla dura (descanso, rachas, convenio…),
    verificado con el auditor (compliance.py) — cero riesgo.
  - JUSTO: prioriza a quien menos carga lleva de ese turno (equidad).
  - MÍNIMA DISRUPCIÓN: prefiere a quien está libre ese día (no genera otro hueco).

  suggest_replacements(problem, schedule, day, shift) → [Candidate]
  cover_absence(problem, schedule, worker_id, days) → huecos + sustitutos

Determinista y explicable. No ejecuta nada: propone; la supervisora decide.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .models import Problem
from .compliance import audit_compliance


@dataclass
class Candidate:
    worker_id: str
    name: str
    legal: bool
    score: int
    disruption: str            # "free" | "reassign" | "blocked"
    why: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def to_dict(self):
        return {"worker_id": self.worker_id, "name": self.name,
                "legal": self.legal, "score": self.score,
                "disruption": self.disruption, "why": self.why,
                "metrics": self.metrics}


def _smap(problem):
    from .engine import _shift_map
    return _shift_map(problem)


def _worker_metrics(problem, schedule, smap, shift):
    """Cuenta por trabajador: turnos de ese tipo, días de trabajo, findes."""
    from .engine import expand_calendar
    days = expand_calendar(problem)
    pairs = [(d, d + 1) for d in range(problem.horizon_days - 1)
             if problem.day(d).dow == 5 and problem.day(d + 1).dow == 6]
    m = {}
    for w in problem.workers:
        row = schedule.get(w.id, {}) or {}
        codes = [row.get(d, problem.rest_code) for d in range(problem.horizon_days)]
        work = sum(1 for c in codes if smap.get(c) and smap[c].is_work)
        of_type = sum(1 for c in codes if c == shift)
        we = sum(1 for (s, u) in pairs
                 if (smap.get(codes[s]) and smap[codes[s]].is_work) or
                    (smap.get(codes[u]) and smap[codes[u]].is_work))
        m[w.id] = {"shift_count": of_type, "work_days": work, "weekends": we}
    return m


def suggest_replacements(problem: Problem, schedule: dict, *, day: int, shift: str,
                         exclude: Optional[str] = None, top: int = 3,
                         rules=None, include_illegal: bool = False,
                         require_skill: Optional[str] = None) -> list[Candidate]:
    rules = rules if rules is not None else problem.rules
    smap = _smap(problem)
    metrics = _worker_metrics(problem, schedule, smap, shift)
    avg_shift = (sum(m["shift_count"] for m in metrics.values()) / max(1, len(metrics)))
    excl = ({exclude} if isinstance(exclude, str) else set(exclude or []))

    cands: list[Candidate] = []
    for w in problem.workers:
        if w.id in excl:
            continue
        if require_skill and require_skill not in w.skills:
            continue        # debe tener el rol/skill exigido (p.ej. enfermera)
        cur = (schedule.get(w.id, {}) or {}).get(day, problem.rest_code)
        if cur == shift:
            continue  # ya lo hace
        why, blocked = [], None

        # restricciones estructurales (no son "reglas", las fuerza el modelo)
        if shift not in problem.allowed_for(w):
            blocked = f"no puede hacer «{shift}»"
        un = w.unavailable.get(day, [])
        if "*" in un or shift in un:
            blocked = "no disponible ese día"
        if day in w.fixed and w.fixed[day] != shift:
            blocked = f"fijado a «{w.fixed[day]}» ese día"

        free = not (smap.get(cur) and smap[cur].is_work)
        disruption = "free" if free else "reassign"

        if blocked:
            if include_illegal:
                cands.append(Candidate(w.id, w.name or w.id, False, -1, "blocked",
                                       [blocked], metrics.get(w.id, {})))
            continue

        # legalidad: ¿asignarle el turno introduce algún incumplimiento DURO suyo?
        trial = {k: dict(v) for k, v in schedule.items()}
        trial.setdefault(w.id, {})[day] = shift
        for ex in excl:
            trial.setdefault(ex, {})[day] = problem.rest_code
        rep = audit_compliance(problem, trial, rules=rules)
        my_hard = [i for c in rep.checks if c.mode == "hard" and c.status == "fail"
                   for i in c.issues if i.worker == w.id]
        legal = not my_hard

        mt = metrics.get(w.id, {"shift_count": 0, "work_days": 0, "weekends": 0})
        if not legal:
            why.append("rompería: " + my_hard[0].detail)
            if not include_illegal:
                continue
        else:
            why.append("cumple convenio (descanso, rachas)")
        why.append("libre ese día" if free else f"tendría que dejar «{cur}»")
        if mt["shift_count"] <= avg_shift:
            why.append(f"poca carga de «{shift}» ({mt['shift_count']} vs media {avg_shift:.0f})")

        score = (1000 if legal else 0) + (200 if free else 0)
        score += int((avg_shift - mt["shift_count"]) * 10) - mt["work_days"]
        cands.append(Candidate(w.id, w.name or w.id, legal, score, disruption,
                               why, mt))

    cands.sort(key=lambda c: (c.legal, c.score), reverse=True)
    return cands[:top] if top else cands


def cover_absence(problem: Problem, schedule: dict, worker_id: str,
                  days: list[int], *, top: int = 3, rules=None) -> dict:
    """
    Una persona se da de baja `days`. Detecta los turnos que quedan por debajo
    del mínimo de cobertura y propone sustitutos para cada hueco.
    """
    res = cover_catastrophe(problem, schedule, {worker_id: list(days)},
                            top=top, rules=rules)
    return {"worker_id": worker_id, "gaps": res["gaps"], "summary": res["summary"]}


def _requirements(problem, rules, d):
    """
    Requisitos de cobertura DUROS de un día: [(shift, skill|None, min)].
    Une 'coverage' (total, opcionalmente por skill/grupo) y 'skill_coverage'
    (por rol). Así las bajas se evalúan también contra la mezcla por rol.
    """
    day = problem.day(d)
    kind = "holiday" if day.is_holiday else ("weekend" if day.is_weekend else "weekday")
    out = {}
    for r in rules:
        if r.mode.value != "hard":
            continue
        p = r.params
        if r.type == "coverage":
            skill = p.get("skill")
            dem = ({int(k): v for k, v in p.get("by_day", {}).items()}.get(d)
                   or p.get("by_daytype", {}).get(kind) or p.get("demand", {}))
            for code, spec in dem.items():
                lo = spec if isinstance(spec, int) else spec.get("min")
                if lo:
                    out[(code, skill)] = max(out.get((code, skill), 0), lo)
        elif r.type == "skill_coverage":
            reqs = (p.get("by_daytype", {}).get(kind, p.get("requirements", []))
                    if p.get("by_daytype") else p.get("requirements", []))
            for req in reqs:
                lo = req.get("min")
                if lo:
                    k = (req["shift"], req.get("skill"))
                    out[k] = max(out.get(k, 0), lo)
    return [(shift, skill, lo) for (shift, skill), lo in out.items()]


def _count_role(problem, schedule, d, shift, skill):
    return sum(1 for w in problem.workers
               if (skill is None or skill in w.skills)
               and (schedule.get(w.id, {}) or {}).get(d) == shift)


# --------------------------------------------------------------------------- #
# Decisiones del día a día (el "Consejero"): librar, cambiar turno, catástrofe
# --------------------------------------------------------------------------- #
def _hard_issue_keys(rep):
    return {(i.rule_id, i.detail) for c in rep.checks
            if c.mode == "hard" and c.status == "fail" for i in c.issues}


def can_release(problem: Problem, schedule: dict, worker_id: str, day: int,
                *, rules=None) -> dict:
    """¿Puede librar esta persona ese día sin romper cobertura/convenio?"""
    rules = rules if rules is not None else problem.rules
    cur = (schedule.get(worker_id, {}) or {}).get(day, problem.rest_code)
    base = _hard_issue_keys(audit_compliance(problem, schedule, rules=rules))
    trial = {k: dict(v) for k, v in schedule.items()}
    trial.setdefault(worker_id, {})[day] = problem.rest_code
    rep = audit_compliance(problem, trial, rules=rules)
    new = [i for c in rep.checks if c.mode == "hard" and c.status == "fail"
           for i in c.issues if (i.rule_id, i.detail) not in base]
    ok = not new
    reason = (f"Sí: {worker_id} puede librar el día {day+1} (no rompe cobertura ni convenio)."
              if ok else f"No: se incumpliría — {new[0].detail}")
    return {"ok": ok, "worker": worker_id, "day": day, "current": cur,
            "blockers": [i.to_dict() for i in new], "reason": reason}


def can_swap(problem: Problem, schedule: dict, worker_a: str, day_a: int,
             worker_b: str, day_b: int, *, rules=None) -> dict:
    """¿Es posible el cambio (worker_a,día_a) ↔ (worker_b,día_b)?"""
    rules = rules if rules is not None else problem.rules
    byid = {w.id: w for w in problem.workers}
    wa, wb = byid.get(worker_a), byid.get(worker_b)
    ca = (schedule.get(worker_a, {}) or {}).get(day_a, problem.rest_code)
    cb = (schedule.get(worker_b, {}) or {}).get(day_b, problem.rest_code)
    work = set(problem.work_shift_codes())
    struct = []
    if wa and cb in work and cb not in problem.allowed_for(wa):
        struct.append(f"{worker_a} no puede hacer «{cb}»")
    if wb and ca in work and ca not in problem.allowed_for(wb):
        struct.append(f"{worker_b} no puede hacer «{ca}»")
    if wa and ("*" in wa.unavailable.get(day_a, []) or cb in wa.unavailable.get(day_a, [])):
        struct.append(f"{worker_a} no disponible el día {day_a+1}")
    if wb and ("*" in wb.unavailable.get(day_b, []) or ca in wb.unavailable.get(day_b, [])):
        struct.append(f"{worker_b} no disponible el día {day_b+1}")
    base = _hard_issue_keys(audit_compliance(problem, schedule, rules=rules))
    trial = {k: dict(v) for k, v in schedule.items()}
    trial.setdefault(worker_a, {})[day_a] = cb
    trial.setdefault(worker_b, {})[day_b] = ca
    rep = audit_compliance(problem, trial, rules=rules)
    new = [i for c in rep.checks if c.mode == "hard" and c.status == "fail"
           for i in c.issues if (i.rule_id, i.detail) not in base]
    ok = not new and not struct
    reason = ("Sí: el cambio respeta convenio y cobertura." if ok
              else "No: " + (struct[0] if struct else new[0].detail))
    return {"ok": ok, "reason": reason,
            "blockers": struct + [i.detail for i in new]}


def cover_catastrophe(problem: Problem, schedule: dict, absences: dict,
                      *, top: int = 3, rules=None) -> dict:
    """
    Varias bajas a la vez. absences = {worker_id: [días]}. Detecta TODOS los
    huecos de cobertura y propone sustitutos (excluyendo a los ausentes).
    """
    rules = rules if rules is not None else problem.rules
    from .engine import expand_calendar
    expand_calendar(problem)
    absent = set(absences)
    trial = {k: dict(v) for k, v in schedule.items()}
    for wid, days in absences.items():
        for d in days:
            trial.setdefault(wid, {})[d] = problem.rest_code

    affected = sorted({d for days in absences.values() for d in days})
    gaps = []
    for d in affected:
        for shift, skill, need in _requirements(problem, rules, d):
            cnt = _count_role(problem, trial, d, shift, skill)
            if cnt < need:
                sugg = suggest_replacements(problem, trial, day=d, shift=shift,
                                            exclude=absent, require_skill=skill,
                                            top=top, rules=rules)
                gaps.append({"day": d, "shift": shift, "skill": skill,
                             "have": cnt, "need": need,
                             "candidates": [c.to_dict() for c in sugg]})
    return {"absent": sorted(absent), "gaps": gaps,
            "summary": {"uncovered": len(gaps)}}
