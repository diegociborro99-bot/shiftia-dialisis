"""
ShiftiaCoreV8 — framework de reglas + reglas built-in.

Cada regla es una función registrada con @register("nombre"). Recibe el
contexto de construcción (BuildContext) y la config de la regla, y AÑADE al
modelo CP-SAT restricciones DURAS (mode=hard) o penalizaciones BLANDAS
(mode=soft) vía variables de holgura que se minimizan en el objetivo.

Novedades v8.1:
  - GUARDAS: en modo "guarded", cada restricción dura se cuelga de un literal
    de guarda por regla. Eso habilita (a) explicar la infeasibilidad (núcleo
    mínimo de reglas en conflicto vía assumptions) y (b) auto-relajar la regla
    dura menos prioritaria cuando no hay solución.
  - HORIZONTE RODANTE: helpers que miran el `history` de cada persona (los días
    inmediatamente anteriores al horizonte) para que rachas, noches seguidas y
    secuencias prohibidas crucen el límite de mes.

Helper clave: ctx.bounded(expr, lb, ub, rule, ...) aplica una cota como dura o
blanda según el modo. Casi toda regla se reduce a "esta expresión entre lb y ub".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ortools.sat.python import cp_model

from .models import Problem, Rule, RuleMode, ShiftType, Worker


# --------------------------------------------------------------------------- #
# Registro de reglas
# --------------------------------------------------------------------------- #
RULES_REGISTRY: dict[str, Callable[["BuildContext", Rule], None]] = {}


def register(name: str):
    def deco(fn):
        RULES_REGISTRY[name] = fn
        return fn
    return deco


def available_rules() -> list[str]:
    return sorted(RULES_REGISTRY.keys())


# --------------------------------------------------------------------------- #
# Penalización (entrada blanda en el objetivo)
# --------------------------------------------------------------------------- #
@dataclass
class Penalty:
    var: Any            # IntVar/BoolVar cuyo valor son las unidades de violación
    base_weight: int
    tier: int
    rule: Rule
    label: str
    ub: int             # cota superior del var (para escalar los tiers)


def _sum(terms):
    """Suma robusta que admite mezclar variables y constantes (0/1)."""
    terms = [t for t in terms if t is not None]
    total = 0
    for t in terms:
        total = total + t
    return total


def _hhmm(s) -> int:
    """'HH:MM' -> minutos desde medianoche."""
    h, m = str(s).split(":")
    return int(h) * 60 + int(m)


def _overlap(a0, a1, b0, b1) -> int:
    return max(0, min(a1, b1) - max(a0, b0))


# --------------------------------------------------------------------------- #
# Contexto de construcción
# --------------------------------------------------------------------------- #
@dataclass
class BuildContext:
    model: cp_model.CpModel
    problem: Problem
    smap: dict[str, ShiftType]
    xvars: dict[tuple, Any]
    worksvars: dict[tuple, Any]
    # índice (worker_id, day) -> [códigos candidatos] para extracción lineal
    cell_codes: dict[tuple, list] = field(default_factory=dict)
    penalties: list[Penalty] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    guarded: bool = False
    guards: dict[str, Any] = field(default_factory=dict)   # rule_id -> BoolVar

    # ---- acceso a variables ----
    def x(self, wid: str, d: int, code: str):
        return self.xvars.get((wid, d, code))

    def works(self, wid: str, d: int):
        return self.worksvars.get((wid, d))

    # ---- guardas (para IIS / relajación) ----
    def guard(self, rule: Rule):
        if not self.guarded:
            return None
        g = self.guards.get(rule.id)
        if g is None:
            g = self.model.NewBoolVar(f"guard:{rule.id}")
            self.guards[rule.id] = g
        return g

    def hard(self, rule: Rule, ct):
        """Registra una restricción dura; en modo guarded la cuelga de la guarda."""
        if self.guarded:
            ct.OnlyEnforceIf(self.guard(rule))
        return ct

    # ---- horizonte rodante (history) ----
    def hist_code(self, w: Worker, d: int):
        """Código del turno en el día d<0 (d=-1 = último del history)."""
        L = len(w.history)
        if d >= 0 or -d > L:
            return None
        return w.history[L + d]

    def work_at(self, w: Worker, d: int):
        """1/0 (constante) si d<0 viene del history; var works si d>=0; None si fuera."""
        if d >= 0:
            return self.works(w.id, d)
        code = self.hist_code(w, d)
        if code is None:
            return None
        s = self.smap.get(code)
        return 1 if (s and s.is_work) else 0

    def shift_at_in(self, w: Worker, d: int, codes):
        """Suma de x de esos códigos en el día d (var si d>=0; const si d<0)."""
        if d >= 0:
            return _sum([self.x(w.id, d, c) for c in codes])
        code = self.hist_code(w, d)
        if code is None:
            return None
        return 1 if code in set(codes) else 0

    # ---- scope ----
    def workers_in_scope(self, rule: Rule) -> list[Worker]:
        sc = rule.scope or {}
        ws = self.problem.workers
        if sc.get("workers"):
            ids = set(sc["workers"])
            ws = [w for w in ws if w.id in ids]
        if sc.get("groups"):
            g = set(sc["groups"])
            ws = [w for w in ws if g.intersection(w.groups)]
        if sc.get("skills"):
            s = set(sc["skills"])
            ws = [w for w in ws if s.intersection(w.skills)]
        return ws

    def days_in_scope(self, rule: Rule) -> list[int]:
        sc = rule.scope or {}
        idxs = list(range(self.problem.horizon_days))
        if sc.get("day_tags"):
            tags = set(sc["day_tags"])
            idxs = [i for i in idxs if tags.intersection(self.problem.day(i).tags)]
        return idxs

    # ---- resolución de turnos ----
    def expand_codes(self, codes=None, period=None, tag=None) -> list[str]:
        out: list[str] = []
        if codes:
            out.extend([codes] if isinstance(codes, str) else codes)
        if period:
            periods = [period] if isinstance(period, str) else period
            out.extend(s.code for s in self.problem.shifts if s.period in periods)
        if tag:
            tags = [tag] if isinstance(tag, str) else tag
            out.extend(s.code for s in self.problem.shifts
                       if set(tags).intersection(s.tags))
        seen, res = set(), []
        for c in out:
            if c not in seen:
                seen.add(c)
                res.append(c)
        return res

    def weekend_pairs(self, weekend_dows=(5, 6)) -> list[tuple[int, int]]:
        first, second = weekend_dows
        pairs = []
        for d in range(self.problem.horizon_days - 1):
            a, b = self.problem.day(d), self.problem.day(d + 1)
            if a.dow == first and b.dow == second:
                pairs.append((d, d + 1))
        return pairs

    # ---- penalización y cota dura-o-blanda ----
    def add_penalty(self, var, base_weight, tier, rule, label, ub):
        self.penalties.append(Penalty(var, int(base_weight), int(tier),
                                      rule, label, int(ub)))

    def bounded(self, expr, *, lb=None, ub=None, rule: Rule, label: str,
                slack_ub: int):
        if rule.mode == RuleMode.HARD:
            if lb is not None:
                self.hard(rule, self.model.Add(expr >= lb))
            if ub is not None:
                self.hard(rule, self.model.Add(expr <= ub))
            return
        if lb is not None:
            s = self.model.NewIntVar(0, int(slack_ub), f"{rule.id}:{label}:under")
            self.model.Add(expr + s >= lb)
            self.add_penalty(s, rule.weight, rule.tier, rule,
                             f"{label} (déficit)", slack_ub)
        if ub is not None:
            o = self.model.NewIntVar(0, int(slack_ub), f"{rule.id}:{label}:over")
            self.model.Add(expr - o <= ub)
            self.add_penalty(o, rule.weight, rule.tier, rule,
                             f"{label} (exceso)", slack_ub)

    def forbid_pair(self, rule: Rule, a, b, label: str):
        """Prohíbe que a y b sean 1 a la vez (a/b pueden ser var o constante)."""
        if a is None or b is None:
            return
        if rule.mode == RuleMode.HARD:
            self.hard(rule, self.model.Add(a + b <= 1))
        else:
            viol = self.model.NewIntVar(0, 1, f"{rule.id}:{label}")
            self.model.Add(a + b - 1 <= viol)
            self.add_penalty(viol, rule.weight, rule.tier, rule, label, 1)


# --------------------------------------------------------------------------- #
# Reglas built-in
# --------------------------------------------------------------------------- #

@register("coverage")
def rule_coverage(ctx: BuildContext, rule: Rule):
    """Cobertura mín/máx por turno y día. Ver README para la forma de `demand`."""
    p = rule.params
    base = p.get("demand", {})
    by_daytype = p.get("by_daytype", {})
    by_day = {int(k): v for k, v in p.get("by_day", {}).items()}
    skill, group = p.get("skill"), p.get("group")
    buffer = int(p.get("buffer", 0))    # sobredimensiona el mínimo (robustez ante bajas)
    workers = ctx.workers_in_scope(rule)
    if skill:
        workers = [w for w in workers if skill in w.skills]
    if group:
        workers = [w for w in workers if group in w.groups]

    def demand_for(d):
        if d in by_day:
            return by_day[d]
        day = ctx.problem.day(d)
        kind = "holiday" if day.is_holiday else ("weekend" if day.is_weekend
                                                 else "weekday")
        return by_daytype.get(kind, base)

    nW = max(1, len(workers))
    for d in ctx.days_in_scope(rule):
        for code, spec in demand_for(d).items():
            lo = spec if isinstance(spec, int) else spec.get("min")
            hi = None if isinstance(spec, int) else spec.get("max")
            if lo is not None and buffer:
                lo = lo + buffer
            count = _sum([ctx.x(w.id, d, code) for w in workers])
            ctx.bounded(count, lb=lo, ub=hi, rule=rule,
                        label=f"cobertura {code} día {d}", slack_ub=nW)


@register("time_coverage")
def rule_time_coverage(ctx: BuildContext, rule: Rule):
    """
    Cobertura por FRANJA HORARIA (genérica — retail, urgencias, call center…):
    en cada franja debe haber al menos `min` personas DE SERVICIO, contando a
    quien tiene un turno cuyo horario solapa la franja. Maneja turnos que cruzan
    medianoche (la parte de madrugada cuenta en el día siguiente).

    params:
      bands:      [{"from":"09:00","to":"13:00","min":3,"max":6}, ...]
      by_daytype: {"weekday":[bands], "weekend":[bands], "holiday":[bands]}
      by_day:     {idx: [bands]}
    """
    p = rule.params
    base = p.get("bands", [])
    by_daytype = p.get("by_daytype", {})
    by_day = {int(k): v for k, v in p.get("by_day", {}).items()}
    workers = ctx.workers_in_scope(rule)
    # tramos por turno (soporta turnos PARTIDOS): parte del mismo día y carry
    spans = {}
    for s in ctx.problem.shifts:
        if not s.is_work:
            continue
        sames, carries = [], []
        for (s0, s1) in s.intervals():
            sames.append((s0, min(s1, 1440)))
            if s1 > 1440:
                carries.append((0, s1 - 1440))
        if sames or carries:
            spans[s.code] = (sames, carries)
    H = ctx.problem.horizon_days
    nW = max(1, len(workers))

    def bands_for(d):
        if d in by_day:
            return by_day[d]
        day = ctx.problem.day(d)
        kind = "holiday" if day.is_holiday else ("weekend" if day.is_weekend else "weekday")
        return by_daytype.get(kind, base)

    for d in ctx.days_in_scope(rule):
        for band in bands_for(d):
            b0, b1 = _hhmm(band["from"]), _hhmm(band["to"])
            lo, hi = band.get("min"), band.get("max")
            terms = []
            for w in workers:
                for code, (sames, carries) in spans.items():
                    if any(_overlap(a, b, b0, b1) > 0 for (a, b) in sames):
                        terms.append(ctx.x(w.id, d, code))
                    if d > 0 and any(_overlap(a, b, b0, b1) > 0 for (a, b) in carries):
                        terms.append(ctx.x(w.id, d - 1, code))
            ctx.bounded(_sum(terms), lb=lo, ub=hi, rule=rule,
                        label=f"franja {band['from']}-{band['to']} día {d}",
                        slack_ub=nW)


@register("max_consecutive_work_days")
def rule_max_consec_work(ctx: BuildContext, rule: Rule):
    """No más de `max` días productivos seguidos (cuenta el history)."""
    n = int(rule.params.get("max", rule.params.get("n", 6)))
    H = ctx.problem.horizon_days
    for w in ctx.workers_in_scope(rule):
        L = len(w.history)
        for start in range(-min(L, n), H - n):
            window = [ctx.work_at(w, start + k) for k in range(n + 1)]
            if all(t is None for t in window):
                continue
            ctx.bounded(_sum(window), ub=n, rule=rule,
                        label=f"{w.id} racha>{n} desde día {start}",
                        slack_ub=n + 1)


@register("max_consecutive_shift")
def rule_max_consec_shift(ctx: BuildContext, rule: Rule):
    """No más de `max` turnos seguidos de cierto tipo (cuenta el history)."""
    n = int(rule.params.get("max", rule.params.get("n", 3)))
    codes = ctx.expand_codes(rule.params.get("shifts"), rule.params.get("period"))
    H = ctx.problem.horizon_days
    for w in ctx.workers_in_scope(rule):
        L = len(w.history)
        for start in range(-min(L, n), H - n):
            terms = [ctx.shift_at_in(w, start + k, codes) for k in range(n + 1)]
            if all(t is None for t in terms):
                continue
            ctx.bounded(_sum(terms), ub=n, rule=rule,
                        label=f"{w.id} {'+'.join(codes)}>{n} desde día {start}",
                        slack_ub=n + 1)


@register("forbidden_sequence")
def rule_forbidden_sequence(ctx: BuildContext, rule: Rule):
    """Prohíbe transición día→día (p.ej. Noche→Mañana). Cuenta el history."""
    p = rule.params
    from_codes = ctx.expand_codes(p.get("from"), p.get("from_period"))
    to_codes = ctx.expand_codes(p.get("to"), p.get("to_period"))
    H = ctx.problem.horizon_days
    for w in ctx.workers_in_scope(rule):
        for d in range(-1, H - 1):
            a = ctx.shift_at_in(w, d, from_codes)
            b = ctx.shift_at_in(w, d + 1, to_codes)
            if a is None or b is None or (isinstance(a, int) and a == 0):
                continue
            ctx.forbid_pair(rule, a, b, f"{w.id} sec prohibida día {d}->{d+1}")


@register("min_rest_hours_between_shifts")
def rule_min_rest_hours(ctx: BuildContext, rule: Rule):
    """
    Descanso mínimo en HORAS reales entre el fin de un turno y el inicio del
    siguiente (usa start/end de los turnos). params: {min_hours: float}
    """
    min_min = int(round(float(rule.params.get("min_hours", 12)) * 60))
    timed = [s for s in ctx.problem.shifts if s.is_work and s.intervals()]
    H = ctx.problem.horizon_days
    for w in ctx.workers_in_scope(rule):
        for d in range(-1, H - 1):
            for s1 in timed:
                for s2 in timed:
                    # descanso entre el FIN del turno (último tramo) y el INICIO
                    # del siguiente (primer tramo) — correcto también si es partido
                    rest = s2.first_start_min() + 1440 - s1.last_end_min()
                    if rest >= min_min:
                        continue
                    a = ctx.shift_at_in(w, d, [s1.code])
                    b = ctx.shift_at_in(w, d + 1, [s2.code])
                    if a is None or b is None or (isinstance(a, int) and a == 0):
                        continue
                    ctx.forbid_pair(rule, a, b,
                                    f"{w.id} descanso<{min_min//60}h {s1.code}->{s2.code} d{d}")


@register("min_free_weekends")
def rule_min_free_weekends(ctx: BuildContext, rule: Rule):
    """Mínimo de findes completos libres (sáb+dom en descanso)."""
    minfree = int(rule.params.get("min", 2))
    dows = tuple(rule.params.get("weekend_dows", (5, 6)))
    pairs = ctx.weekend_pairs(dows)
    if not pairs:
        ctx.notes.append("min_free_weekends: sin info de dow; regla ignorada.")
        return
    for w in ctx.workers_in_scope(rule):
        flags = []
        for (sat, sun) in pairs:
            ws_, wu_ = ctx.works(w.id, sat), ctx.works(w.id, sun)
            f = ctx.model.NewBoolVar(f"{rule.id}:{w.id}:free_we:{sat}")
            ctx.model.Add(ws_ == 0).OnlyEnforceIf(f)
            ctx.model.Add(wu_ == 0).OnlyEnforceIf(f)
            ctx.model.AddBoolOr([ws_, wu_]).OnlyEnforceIf(f.Not())
            flags.append(f)
        ctx.bounded(_sum(flags), lb=min(minfree, len(pairs)), rule=rule,
                    label=f"{w.id} findes libres", slack_ub=len(pairs))


@register("max_weekends_worked")
def rule_max_weekends_worked(ctx: BuildContext, rule: Rule):
    """Tope de findes en los que se trabaja (sáb o dom). params: {max:int}"""
    hi = int(rule.params.get("max", 2))
    dows = tuple(rule.params.get("weekend_dows", (5, 6)))
    pairs = ctx.weekend_pairs(dows)
    if not pairs:
        ctx.notes.append("max_weekends_worked: sin info de dow; regla ignorada.")
        return
    for w in ctx.workers_in_scope(rule):
        flags = []
        for (sat, sun) in pairs:
            ws_, wu_ = ctx.works(w.id, sat), ctx.works(w.id, sun)
            wk = ctx.model.NewBoolVar(f"{rule.id}:{w.id}:we_work:{sat}")
            ctx.model.AddBoolOr([ws_, wu_]).OnlyEnforceIf(wk)
            ctx.model.Add(ws_ == 0).OnlyEnforceIf(wk.Not())
            ctx.model.Add(wu_ == 0).OnlyEnforceIf(wk.Not())
            flags.append(wk)
        ctx.bounded(_sum(flags), ub=hi, rule=rule,
                    label=f"{w.id} findes trabajados", slack_ub=len(pairs))


@register("no_mixed_weekends")
def rule_no_mixed_weekends(ctx: BuildContext, rule: Rule):
    """Sábado y domingo con el mismo estado (ambos trabajo o ambos libres)."""
    dows = tuple(rule.params.get("weekend_dows", (5, 6)))
    pairs = ctx.weekend_pairs(dows)
    if not pairs:
        ctx.notes.append("no_mixed_weekends: sin info de dow; regla ignorada.")
        return
    for w in ctx.workers_in_scope(rule):
        for (sat, sun) in pairs:
            a, b = ctx.works(w.id, sat), ctx.works(w.id, sun)
            if rule.mode == RuleMode.HARD:
                ctx.hard(rule, ctx.model.Add(a == b))
            else:
                diff = ctx.model.NewIntVar(0, 1, f"{rule.id}:{w.id}:mix:{sat}")
                ctx.model.Add(a - b <= diff)
                ctx.model.Add(b - a <= diff)
                ctx.add_penalty(diff, rule.weight, rule.tier, rule,
                                f"{w.id} finde mixto día {sat}", 1)


@register("no_isolated_work")
def rule_no_isolated_work(ctx: BuildContext, rule: Rule):
    """Nada de días de trabajo aislados (libre-trabajo-libre). Cuenta history."""
    H = ctx.problem.horizon_days
    for w in ctx.workers_in_scope(rule):
        for d in range(H):
            prev, nxt = ctx.work_at(w, d - 1), ctx.work_at(w, d + 1)
            if prev is None or nxt is None:
                continue
            cur = ctx.works(w.id, d)
            if rule.mode == RuleMode.HARD:
                ctx.hard(rule, ctx.model.Add(cur <= prev + nxt))
            else:
                v = ctx.model.NewIntVar(0, 1, f"{rule.id}:{w.id}:iso_w:{d}")
                ctx.model.Add(cur - prev - nxt <= v)
                ctx.add_penalty(v, rule.weight, rule.tier, rule,
                                f"{w.id} trabajo aislado día {d}", 1)


@register("no_isolated_rest")
def rule_no_isolated_rest(ctx: BuildContext, rule: Rule):
    """Nada de descansos aislados (trabajo-libre-trabajo). Cuenta history."""
    H = ctx.problem.horizon_days
    for w in ctx.workers_in_scope(rule):
        for d in range(H):
            prev, nxt = ctx.work_at(w, d - 1), ctx.work_at(w, d + 1)
            if prev is None or nxt is None:
                continue
            cur = ctx.works(w.id, d)
            if rule.mode == RuleMode.HARD:
                ctx.hard(rule, ctx.model.Add(cur >= prev + nxt - 1))
            else:
                v = ctx.model.NewIntVar(0, 1, f"{rule.id}:{w.id}:iso_r:{d}")
                ctx.model.Add(prev + nxt - 1 - cur <= v)
                ctx.add_penalty(v, rule.weight, rule.tier, rule,
                                f"{w.id} descanso aislado día {d}", 1)


@register("contract_hours")
def rule_contract_hours(ctx: BuildContext, rule: Rule):
    """Horas trabajadas cerca del contrato. Escala x10 (admite medias horas)."""
    SC = 10
    p = rule.params
    g_target, g_min, g_max = p.get("target"), p.get("min"), p.get("max")
    max_h = max((s.hours for s in ctx.problem.shifts), default=0)
    ub_hours = int(round(ctx.problem.horizon_days * max_h * SC)) + 1
    for w in ctx.workers_in_scope(rule):
        terms = []
        for d in range(ctx.problem.horizon_days):
            for s in ctx.problem.shifts:
                if not s.is_work or not s.hours:
                    continue
                v = ctx.x(w.id, d, s.code)
                if v is not None:
                    terms.append(int(round(s.hours * SC)) * v)
        total = _sum(terms)
        target = w.contract_hours if w.contract_hours is not None else g_target
        wmin = w.min_hours if w.min_hours is not None else g_min
        wmax = w.max_hours if w.max_hours is not None else g_max
        if wmin is not None:
            ctx.bounded(total, lb=int(round(wmin * SC)), rule=rule,
                        label=f"{w.id} horas min", slack_ub=ub_hours)
        if wmax is not None:
            ctx.bounded(total, ub=int(round(wmax * SC)), rule=rule,
                        label=f"{w.id} horas max", slack_ub=ub_hours)
        if target is not None and wmin is None and wmax is None:
            tgt = int(round(target * SC))
            ctx.bounded(total, lb=tgt, ub=tgt, rule=rule,
                        label=f"{w.id} horas vs contrato", slack_ub=ub_hours)


@register("max_shifts_of_type")
def rule_max_shifts_of_type(ctx: BuildContext, rule: Rule):
    """Tope/mínimo de un tipo de turno por persona. params: {shifts|period, max, min}"""
    codes = ctx.expand_codes(rule.params.get("shifts"), rule.params.get("period"))
    hi, lo = rule.params.get("max"), rule.params.get("min")
    H = ctx.problem.horizon_days
    for w in ctx.workers_in_scope(rule):
        terms = [ctx.x(w.id, d, c) for d in range(H) for c in codes]
        ctx.bounded(_sum(terms), lb=lo, ub=hi, rule=rule,
                    label=f"{w.id} #{'+'.join(codes)}", slack_ub=H)


@register("skill_coverage")
def rule_skill_coverage(ctx: BuildContext, rule: Rule):
    """
    Cobertura por SKILL dentro de un turno (skill-mix). Ej.: en «N» se necesitan
    ≥2 con skill 'enfermera' y ≥1 con 'ACLS'.
    params:
      requirements: [{"shift":"N","skill":"ACLS","min":1,"max":?}, ...]
      by_daytype:   {"weekday":[reqs], "weekend":[reqs], "holiday":[reqs]}
    """
    p = rule.params
    base = p.get("requirements", [])
    by_daytype = p.get("by_daytype", {})
    workers = ctx.workers_in_scope(rule)
    nW = max(1, len(workers))

    def reqs_for(d):
        if by_daytype:
            day = ctx.problem.day(d)
            kind = "holiday" if day.is_holiday else ("weekend" if day.is_weekend else "weekday")
            return by_daytype.get(kind, base)
        return base

    for d in ctx.days_in_scope(rule):
        for r in reqs_for(d):
            shift = r["shift"]
            skill = r.get("skill")
            lo, hi = r.get("min"), r.get("max")
            elig = [w for w in workers if (skill is None or skill in w.skills)]
            count = _sum([ctx.x(w.id, d, shift) for w in elig])
            ctx.bounded(count, lb=lo, ub=hi, rule=rule,
                        label=f"{shift}/{skill or '*'} día {d}", slack_ub=nW)


@register("max_hours_in_window")
def rule_max_hours_window(ctx: BuildContext, rule: Rule):
    """Máximo de horas trabajadas en cualquier ventana móvil de N días (carry-over)."""
    SC = 10
    W = int(rule.params.get("days", 7))
    maxc = int(round(float(rule.params.get("max_hours", 48)) * SC))
    H = ctx.problem.horizon_days
    work_shifts = [s for s in ctx.problem.shifts if s.is_work and s.hours]
    ub_slack = int(round(W * max((s.hours for s in work_shifts), default=0) * SC)) + 1
    for w in ctx.workers_in_scope(rule):
        L = len(w.history)
        for start in range(max(-L, -(W - 1)), H - W + 1):
            terms = []
            has_var = False
            for k in range(W):
                d = start + k
                if d >= 0:
                    has_var = True
                    for s in work_shifts:
                        v = ctx.x(w.id, d, s.code)
                        if v is not None:
                            terms.append(int(round(s.hours * SC)) * v)
                else:
                    code = ctx.hist_code(w, d)
                    s = ctx.smap.get(code)
                    if s and s.is_work and s.hours:
                        terms.append(int(round(s.hours * SC)))
            if has_var:
                ctx.bounded(_sum(terms), ub=maxc, rule=rule,
                            label=f"{w.id} horas/{W}d desde día {start}", slack_ub=ub_slack)


@register("min_rest_days_in_window")
def rule_min_rest_days_window(ctx: BuildContext, rule: Rule):
    """Mínimo de días de descanso en cada ventana móvil de N días (descanso semanal)."""
    W = int(rule.params.get("days", 7))
    minrest = int(rule.params.get("min_rest", 1))
    H = ctx.problem.horizon_days
    for w in ctx.workers_in_scope(rule):
        L = len(w.history)
        for start in range(max(-L, -(W - 1)), H - W + 1):
            works = [ctx.work_at(w, start + k) for k in range(W)]
            if all(t is None for t in works) or not any(
                    (start + k) >= 0 for k in range(W)):
                continue
            # sum(works) <= W - minrest  ⇔  al menos minrest descansos
            ctx.bounded(_sum(works), ub=W - minrest, rule=rule,
                        label=f"{w.id} descanso/{W}d desde día {start}", slack_ub=W)


@register("same_shift_forbidden")
def rule_same_shift_forbidden(ctx: BuildContext, rule: Rule):
    """Dos personas que no pueden coincidir en el mismo turno. params:{pairs,shifts}"""
    pairs = rule.params.get("pairs", [])
    codes = ctx.expand_codes(rule.params.get("shifts")) or ctx.problem.work_shift_codes()
    H = ctx.problem.horizon_days
    for (a, b) in pairs:
        for d in range(H):
            for c in codes:
                ctx.forbid_pair(rule, ctx.x(a, d, c), ctx.x(b, d, c),
                                f"{a}&{b} juntos {c} día {d}")


@register("same_shift_required")
def rule_same_shift_required(ctx: BuildContext, rule: Rule):
    """Dos personas que deben hacer el mismo turno (mentor/becario). params:{pairs}"""
    pairs = rule.params.get("pairs", [])
    codes = ctx.problem.work_shift_codes() + [ctx.problem.rest_code]
    H = ctx.problem.horizon_days
    for (a, b) in pairs:
        for d in range(H):
            for c in codes:
                xa, xb = ctx.x(a, d, c), ctx.x(b, d, c)
                if xa is None or xb is None:
                    continue
                if rule.mode == RuleMode.HARD:
                    ctx.hard(rule, ctx.model.Add(xa == xb))
                else:
                    v = ctx.model.NewIntVar(0, 1, f"{rule.id}:{a}{b}:{c}:{d}")
                    ctx.model.Add(xa - xb <= v)
                    ctx.model.Add(xb - xa <= v)
                    ctx.add_penalty(v, rule.weight, rule.tier, rule,
                                    f"{a}!={b} en {c} día {d}", 1)


@register("balance")
def rule_balance(ctx: BuildContext, rule: Rule):
    """
    Reparto equitativo (BLANDO): minimiza la diferencia max-min entre personas.
    params: {dimension: "shift"|"weekend"|"holiday"|"work", shifts:[codes]}
    """
    dim = rule.params.get("dimension", "shift")
    codes = ctx.expand_codes(rule.params.get("shifts"), rule.params.get("period"))
    workers = ctx.workers_in_scope(rule)
    if len(workers) < 2:
        return
    H = ctx.problem.horizon_days

    def count_expr(w):
        if dim == "shift":
            return _sum([ctx.x(w.id, d, c) for d in range(H) for c in codes])
        if dim == "work":
            return _sum([ctx.works(w.id, d) for d in range(H)])
        if dim in ("weekend", "holiday"):
            sel = [d for d in range(H)
                   if (ctx.problem.day(d).is_weekend if dim == "weekend"
                       else ctx.problem.day(d).is_holiday)]
            return _sum([ctx.works(w.id, d) for d in sel])
        return 0

    # prior: contadores YA acumulados (p.ej. noches en lo que va de año) para
    # repartir a lo largo del AÑO, no solo del horizonte (equidad con memoria).
    prior = rule.params.get("prior", {}) or {}
    maxp = max([int(v) for v in prior.values()], default=0)
    UB = H + maxp
    counts = []
    for w in workers:
        c = ctx.model.NewIntVar(0, H, f"{rule.id}:{w.id}:cnt")
        ctx.model.Add(c == count_expr(w))
        if prior:
            t = ctx.model.NewIntVar(0, UB, f"{rule.id}:{w.id}:tot")
            ctx.model.Add(t == c + int(prior.get(w.id, 0)))
            counts.append(t)
        else:
            counts.append(c)
    cmax = ctx.model.NewIntVar(0, UB, f"{rule.id}:max")
    cmin = ctx.model.NewIntVar(0, UB, f"{rule.id}:min")
    ctx.model.AddMaxEquality(cmax, counts)
    ctx.model.AddMinEquality(cmin, counts)
    spread = ctx.model.NewIntVar(0, UB, f"{rule.id}:spread")
    ctx.model.Add(spread == cmax - cmin)
    ctx.add_penalty(spread, rule.weight, rule.tier, rule,
                    f"reparto {dim} {'+'.join(codes)}{' (anual)' if prior else ''} (spread)", UB)


@register("shift_stability")
def rule_shift_stability(ctx: BuildContext, rule: Rule):
    """
    Penaliza (BLANDO) cambiar de tipo de turno de un día al siguiente cuando se
    trabaja ambos: favorece bloques homogéneos (M-M-M mejor que M-T-N).
    """
    codes = ctx.problem.work_shift_codes()
    H = ctx.problem.horizon_days
    for w in ctx.workers_in_scope(rule):
        for d in range(H - 1):
            wd, wn = ctx.works(w.id, d), ctx.works(w.id, d + 1)
            same_terms = []
            for c in codes:
                xa, xb = ctx.x(w.id, d, c), ctx.x(w.id, d + 1, c)
                if xa is None or xb is None:
                    continue
                bc = ctx.model.NewBoolVar(f"{rule.id}:{w.id}:{c}:{d}")
                ctx.model.Add(bc <= xa)
                ctx.model.Add(bc <= xb)
                ctx.model.Add(bc >= xa + xb - 1)
                same_terms.append(bc)
            same = _sum(same_terms)
            switch = ctx.model.NewIntVar(0, 1, f"{rule.id}:{w.id}:sw:{d}")
            ctx.model.Add(switch >= wd + wn - 1 - same)
            ctx.add_penalty(switch, rule.weight, rule.tier, rule,
                            f"{w.id} cambia de turno día {d}->{d+1}", 1)


@register("preferences")
def rule_preferences(ctx: BuildContext, rule: Rule):
    """Preferencias por persona (BLANDO). weight>0 prefiere; weight<0 evita."""
    for w in ctx.workers_in_scope(rule):
        for pref in w.preferences:
            v = ctx.x(w.id, pref.day, pref.shift)
            if v is None:
                continue
            wgt = abs(int(pref.weight)) * max(1, int(rule.weight))
            if pref.weight >= 0:
                miss = ctx.model.NewIntVar(0, 1,
                    f"{rule.id}:{w.id}:pref:{pref.day}:{pref.shift}")
                ctx.model.Add(miss == 1 - v)
                ctx.add_penalty(miss, wgt, rule.tier, rule,
                                f"{w.id} quería {pref.shift} día {pref.day}", 1)
            else:
                ctx.add_penalty(v, wgt, rule.tier, rule,
                                f"{w.id} evitaba {pref.shift} día {pref.day}", 1)


@register("forbid_assignment")
def rule_forbid_assignment(ctx: BuildContext, rule: Rule):
    """Prohíbe asignaciones concretas. params:{assignments:[{worker,day,shift}]}"""
    for a in rule.params.get("assignments", []):
        v = ctx.x(a["worker"], int(a["day"]), a["shift"])
        if v is None:
            continue
        if rule.mode == RuleMode.HARD:
            ctx.hard(rule, ctx.model.Add(v == 0))
        else:
            ctx.add_penalty(v, rule.weight, rule.tier, rule,
                            f"{a['worker']} no {a['shift']} día {a['day']}", 1)


@register("force_assignment")
def rule_force_assignment(ctx: BuildContext, rule: Rule):
    """Fuerza asignaciones concretas. params:{assignments:[{worker,day,shift}]}"""
    for a in rule.params.get("assignments", []):
        v = ctx.x(a["worker"], int(a["day"]), a["shift"])
        if v is None:
            ctx.notes.append(f"force_assignment: {a} no asignable; ignorado.")
            continue
        if rule.mode == RuleMode.HARD:
            ctx.hard(rule, ctx.model.Add(v == 1))
        else:
            miss = ctx.model.NewIntVar(0, 1, f"{rule.id}:force:{a['worker']}:{a['day']}")
            ctx.model.Add(miss == 1 - v)
            ctx.add_penalty(miss, rule.weight, rule.tier, rule,
                            f"{a['worker']} debía {a['shift']} día {a['day']}", 1)
