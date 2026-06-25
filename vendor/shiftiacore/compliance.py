"""
ShiftiaCoreV8 — auditoría de cumplimiento ("convenio as code").

Verificador INDEPENDIENTE del solver: dada una planilla cualquiera (la haga
quien la haga — el motor, la supervisora a mano, una importada de Actais o de
otro sistema), comprueba si cumple las reglas DURAS y devuelve, regla por regla,
si pasa o falla, con la **cita legal** y la lista exacta de incumplimientos.

Por qué importa: en un hospital el dolor es el riesgo con el convenio/Estatuto
Marco. Esto da una **garantía demostrable**: "esta planilla cumple/incumple, y
este turno incumple el Art. 14 (descanso de 12h)". Aprovecha que las reglas ya
están definidas como datos; cada regla puede llevar `citation`.

audit_compliance(problem, schedule, rules=None) → ComplianceReport
  - schedule: {worker_id: {day_index: code}}
  - compliant = no hay ninguna regla DURA incumplida.
Las reglas blandas se reportan como "advisory" (calidad), no rompen cumplimiento.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional

from .models import Problem, Rule, RuleMode


@dataclass
class ComplianceIssue:
    rule_id: str
    detail: str
    worker: Optional[str] = None
    day: Optional[int] = None

    def to_dict(self):
        return {"rule_id": self.rule_id, "detail": self.detail,
                "worker": self.worker, "day": self.day}


@dataclass
class RuleCheck:
    rule_id: str
    rule_type: str
    mode: str
    status: str                      # pass | fail | advisory | unchecked
    issues: list[ComplianceIssue] = field(default_factory=list)
    citation: Optional[dict] = None

    def to_dict(self):
        return {"rule_id": self.rule_id, "rule_type": self.rule_type,
                "mode": self.mode, "status": self.status,
                "citation": self.citation, "count": len(self.issues),
                "issues": [i.to_dict() for i in self.issues[:20]]}


@dataclass
class ComplianceReport:
    compliant: bool
    checks: list[RuleCheck]
    summary: dict

    def to_dict(self):
        return {"compliant": self.compliant, "summary": self.summary,
                "checks": [c.to_dict() for c in self.checks]}

    def certificate(self) -> str:
        head = ("✅ PLANILLA CONFORME" if self.compliant
                else "❌ PLANILLA NO CONFORME")
        lines = [head, ""]
        for c in self.checks:
            mark = {"pass": "✓", "fail": "✗", "advisory": "·",
                    "unchecked": "?"}[c.status]
            ref = f" [{c.citation.get('ref')}]" if c.citation and c.citation.get("ref") else ""
            extra = f" — {len(c.issues)} incidencia(s)" if c.issues else ""
            lines.append(f"{mark} {c.rule_id} ({c.mode}){ref}{extra}")
            for i in c.issues[:3]:
                lines.append(f"     · {i.detail}")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Contexto de auditoría
# --------------------------------------------------------------------------- #
class _Ctx:
    def __init__(self, problem: Problem, schedule: dict):
        from .engine import expand_calendar, _shift_map
        self.p = problem
        self.schedule = schedule or {}
        self.days = expand_calendar(problem)
        self.smap = _shift_map(problem)
        self.rest = problem.rest_code
        self.by_id = {w.id: w for w in problem.workers}

    def code_at(self, wid, d):
        if d >= 0:
            return (self.schedule.get(wid, {}) or {}).get(d, self.rest) or self.rest
        w = self.by_id.get(wid)
        if not w:
            return None
        L = len(w.history)
        return w.history[L + d] if -d <= L else None

    def is_work(self, code):
        s = self.smap.get(code)
        return bool(s and s.is_work)

    def expand_codes(self, codes=None, period=None):
        out = []
        if codes:
            out += [codes] if isinstance(codes, str) else list(codes)
        if period:
            per = [period] if isinstance(period, str) else period
            out += [s.code for s in self.p.shifts if s.period in per]
        seen, res = set(), []
        for c in out:
            if c not in seen:
                seen.add(c); res.append(c)
        return res

    def workers_in_scope(self, rule):
        sc = rule.scope or {}
        ws = self.p.workers
        if sc.get("workers"):
            ids = set(sc["workers"]); ws = [w for w in ws if w.id in ids]
        if sc.get("groups"):
            g = set(sc["groups"]); ws = [w for w in ws if g.intersection(w.groups)]
        if sc.get("skills"):
            s = set(sc["skills"]); ws = [w for w in ws if s.intersection(w.skills)]
        return ws

    def days_in_scope(self, rule):
        sc = rule.scope or {}
        idxs = list(range(self.p.horizon_days))
        if sc.get("day_tags"):
            tags = set(sc["day_tags"])
            idxs = [i for i in idxs if tags.intersection(self.p.day(i).tags)]
        return idxs

    def weekend_pairs(self, dows=(5, 6)):
        a, b = dows
        return [(d, d + 1) for d in range(self.p.horizon_days - 1)
                if self.p.day(d).dow == a and self.p.day(d + 1).dow == b]


# --------------------------------------------------------------------------- #
# Checkers por tipo de regla
# --------------------------------------------------------------------------- #
CHECKERS: dict[str, Callable] = {}


def _check(name):
    def deco(fn):
        CHECKERS[name] = fn
        return fn
    return deco


@_check("coverage")
def _c_coverage(ctx, rule):
    p = rule.params
    base = p.get("demand", {})
    by_daytype = p.get("by_daytype", {})
    by_day = {int(k): v for k, v in p.get("by_day", {}).items()}
    skill, group = p.get("skill"), p.get("group")
    workers = ctx.workers_in_scope(rule)
    if skill:
        workers = [w for w in workers if skill in w.skills]
    if group:
        workers = [w for w in workers if group in w.groups]
    issues = []
    for d in ctx.days_in_scope(rule):
        day = ctx.p.day(d)
        kind = "holiday" if day.is_holiday else ("weekend" if day.is_weekend else "weekday")
        dem = by_day.get(d) or by_daytype.get(kind) or base
        for code, spec in dem.items():
            lo = spec if isinstance(spec, int) else spec.get("min")
            hi = None if isinstance(spec, int) else spec.get("max")
            cnt = sum(1 for w in workers if ctx.code_at(w.id, d) == code)
            if lo is not None and cnt < lo:
                issues.append(ComplianceIssue(rule.id, f"día {d+1}: «{code}» {cnt}/{lo} (faltan {lo-cnt})", day=d))
            if hi is not None and cnt > hi:
                issues.append(ComplianceIssue(rule.id, f"día {d+1}: «{code}» {cnt}>{hi} (sobran {cnt-hi})", day=d))
    return issues


def _hhmm(s):
    h, m = str(s).split(":")
    return int(h) * 60 + int(m)


def _ovl(a0, a1, b0, b1):
    return max(0, min(a1, b1) - max(a0, b0))


@_check("time_coverage")
def _c_time_coverage(ctx, rule):
    p = rule.params
    base = p.get("bands", [])
    by_daytype = p.get("by_daytype", {})
    by_day = {int(k): v for k, v in p.get("by_day", {}).items()}
    workers = ctx.workers_in_scope(rule)
    spans = {}
    for s in ctx.p.shifts:
        if not s.is_work:
            continue
        sames, carries = [], []
        for (s0, s1) in s.intervals():
            sames.append((s0, min(s1, 1440)))
            if s1 > 1440:
                carries.append((0, s1 - 1440))
        if sames or carries:
            spans[s.code] = (sames, carries)
    issues = []
    for d in ctx.days_in_scope(rule):
        day = ctx.p.day(d)
        kind = "holiday" if day.is_holiday else ("weekend" if day.is_weekend else "weekday")
        bands = by_day.get(d) or by_daytype.get(kind) or base
        for band in bands:
            b0, b1 = _hhmm(band["from"]), _hhmm(band["to"])
            lo, hi = band.get("min"), band.get("max")
            cnt = 0
            for w in workers:
                sames, carries = spans.get(ctx.code_at(w.id, d), ([], []))
                if any(_ovl(a, b, b0, b1) > 0 for (a, b) in sames):
                    cnt += 1
                    continue
                if d > 0:
                    pc = ctx.code_at(w.id, d - 1)
                    _, pcarry = spans.get(pc, ([], []))
                    if any(_ovl(a, b, b0, b1) > 0 for (a, b) in pcarry):
                        cnt += 1
            if lo is not None and cnt < lo:
                issues.append(ComplianceIssue(rule.id, f"día {d+1} {band['from']}-{band['to']}: {cnt}/{lo} de servicio (faltan {lo-cnt})", day=d))
            if hi is not None and cnt > hi:
                issues.append(ComplianceIssue(rule.id, f"día {d+1} {band['from']}-{band['to']}: {cnt}>{hi}", day=d))
    return issues


def _runs_of(ctx, w, predicate):
    """Longitud de la racha máxima (incluye history) según predicate(code)."""
    L = len(w.history)
    run, best, best_end = 0, 0, None
    for d in range(-L, ctx.p.horizon_days):
        code = ctx.code_at(w.id, d)
        if code is None:
            run = 0; continue
        if predicate(code):
            run += 1
            if run > best:
                best, best_end = run, d
        else:
            run = 0
    return best, best_end


@_check("max_consecutive_work_days")
def _c_consec_work(ctx, rule):
    n = int(rule.params.get("max", rule.params.get("n", 6)))
    issues = []
    for w in ctx.workers_in_scope(rule):
        best, end = _runs_of(ctx, w, ctx.is_work)
        if best > n:
            issues.append(ComplianceIssue(rule.id, f"{w.name or w.id}: racha de {best} días seguidos (máx {n})", worker=w.id, day=end))
    return issues


@_check("max_consecutive_shift")
def _c_consec_shift(ctx, rule):
    n = int(rule.params.get("max", rule.params.get("n", 3)))
    codes = set(ctx.expand_codes(rule.params.get("shifts"), rule.params.get("period")))
    issues = []
    for w in ctx.workers_in_scope(rule):
        best, end = _runs_of(ctx, w, lambda c: c in codes)
        if best > n:
            issues.append(ComplianceIssue(rule.id, f"{w.name or w.id}: {best} «{'+'.join(codes)}» seguidos (máx {n})", worker=w.id, day=end))
    return issues


@_check("forbidden_sequence")
def _c_forbidden_seq(ctx, rule):
    frm = set(ctx.expand_codes(rule.params.get("from"), rule.params.get("from_period")))
    to = set(ctx.expand_codes(rule.params.get("to"), rule.params.get("to_period")))
    issues = []
    for w in ctx.workers_in_scope(rule):
        for d in range(-1, ctx.p.horizon_days - 1):
            a, b = ctx.code_at(w.id, d), ctx.code_at(w.id, d + 1)
            if a in frm and b in to:
                issues.append(ComplianceIssue(rule.id, f"{w.name or w.id}: «{a}»→«{b}» entre día {d+1} y {d+2}", worker=w.id, day=d + 1))
    return issues


@_check("min_rest_hours_between_shifts")
def _c_min_rest(ctx, rule):
    min_min = int(round(float(rule.params.get("min_hours", 12)) * 60))
    issues = []
    for w in ctx.workers_in_scope(rule):
        for d in range(-1, ctx.p.horizon_days - 1):
            a, b = ctx.code_at(w.id, d), ctx.code_at(w.id, d + 1)
            sa, sb = ctx.smap.get(a), ctx.smap.get(b)
            if not (sa and sb and sa.is_work and sb.is_work):
                continue
            if sa.last_end_min() is None or sb.first_start_min() is None:
                continue
            rest = sb.first_start_min() + 1440 - sa.last_end_min()
            if rest < min_min:
                issues.append(ComplianceIssue(rule.id, f"{w.name or w.id}: {rest//60}h entre «{a}» y «{b}» (mín {min_min//60}h) día {d+2}", worker=w.id, day=d + 1))
    return issues


@_check("min_free_weekends")
def _c_min_free_we(ctx, rule):
    minfree = int(rule.params.get("min", 2))
    pairs = ctx.weekend_pairs(tuple(rule.params.get("weekend_dows", (5, 6))))
    if not pairs:
        return []
    issues = []
    for w in ctx.workers_in_scope(rule):
        free = sum(1 for (s, u) in pairs
                   if not ctx.is_work(ctx.code_at(w.id, s)) and not ctx.is_work(ctx.code_at(w.id, u)))
        need = min(minfree, len(pairs))
        if free < need:
            issues.append(ComplianceIssue(rule.id, f"{w.name or w.id}: {free} findes libres (mín {need})", worker=w.id))
    return issues


@_check("max_weekends_worked")
def _c_max_we_worked(ctx, rule):
    hi = int(rule.params.get("max", 2))
    pairs = ctx.weekend_pairs(tuple(rule.params.get("weekend_dows", (5, 6))))
    if not pairs:
        return []
    issues = []
    for w in ctx.workers_in_scope(rule):
        worked = sum(1 for (s, u) in pairs
                     if ctx.is_work(ctx.code_at(w.id, s)) or ctx.is_work(ctx.code_at(w.id, u)))
        if worked > hi:
            issues.append(ComplianceIssue(rule.id, f"{w.name or w.id}: {worked} findes trabajados (máx {hi})", worker=w.id))
    return issues


@_check("skill_coverage")
def _c_skill_coverage(ctx, rule):
    base = rule.params.get("requirements", [])
    by_daytype = rule.params.get("by_daytype", {})
    workers = ctx.workers_in_scope(rule)
    issues = []
    for d in ctx.days_in_scope(rule):
        if by_daytype:
            day = ctx.p.day(d)
            kind = "holiday" if day.is_holiday else ("weekend" if day.is_weekend else "weekday")
            reqs = by_daytype.get(kind, base)
        else:
            reqs = base
        for r in reqs:
            shift, skill = r["shift"], r.get("skill")
            lo, hi = r.get("min"), r.get("max")
            cnt = sum(1 for w in workers
                      if (skill is None or skill in w.skills) and ctx.code_at(w.id, d) == shift)
            if lo is not None and cnt < lo:
                issues.append(ComplianceIssue(rule.id, f"día {d+1}: «{shift}»/{skill or '*'} {cnt}/{lo} (faltan {lo-cnt})", day=d))
            if hi is not None and cnt > hi:
                issues.append(ComplianceIssue(rule.id, f"día {d+1}: «{shift}»/{skill or '*'} {cnt}>{hi}", day=d))
    return issues


@_check("max_hours_in_window")
def _c_max_hours_window(ctx, rule):
    W = int(rule.params.get("days", 7))
    maxh = float(rule.params.get("max_hours", 48))
    H = ctx.p.horizon_days
    issues = []
    for w in ctx.workers_in_scope(rule):
        L = len(w.history)
        for start in range(max(-L, -(W - 1)), H - W + 1):
            if not any((start + k) >= 0 for k in range(W)):
                continue
            tot, known = 0.0, False
            for k in range(W):
                code = ctx.code_at(w.id, start + k)
                if code is None:
                    continue
                known = True
                s = ctx.smap.get(code)
                if s and s.is_work and s.hours:
                    tot += s.hours
            if known and tot > maxh + 1e-9:
                issues.append(ComplianceIssue(rule.id, f"{w.name or w.id}: {tot:g}h en {W}d (máx {maxh:g}) desde día {max(0,start)+1}", worker=w.id, day=max(0, start)))
    return issues


@_check("min_rest_days_in_window")
def _c_min_rest_window(ctx, rule):
    W = int(rule.params.get("days", 7))
    minrest = int(rule.params.get("min_rest", 1))
    H = ctx.p.horizon_days
    issues = []
    for w in ctx.workers_in_scope(rule):
        L = len(w.history)
        for start in range(max(-L, -(W - 1)), H - W + 1):
            works = known = 0
            for k in range(W):
                code = ctx.code_at(w.id, start + k)
                if code is None:
                    continue
                known += 1
                s = ctx.smap.get(code)
                if s and s.is_work:
                    works += 1
            if known == W and (known - works) < minrest:
                issues.append(ComplianceIssue(rule.id, f"{w.name or w.id}: {known-works} descanso(s) en {W}d (mín {minrest}) desde día {max(0,start)+1}", worker=w.id, day=max(0, start)))
    return issues


@_check("contract_hours")
def _c_contract(ctx, rule):
    p = rule.params
    g_target, g_min, g_max = p.get("target"), p.get("min"), p.get("max")
    issues = []
    for w in ctx.workers_in_scope(rule):
        total = 0.0
        for d in range(ctx.p.horizon_days):
            s = ctx.smap.get(ctx.code_at(w.id, d))
            if s and s.is_work:
                total += s.hours or 0
        wmin = w.min_hours if w.min_hours is not None else g_min
        wmax = w.max_hours if w.max_hours is not None else g_max
        if wmin is not None and total < wmin:
            issues.append(ComplianceIssue(rule.id, f"{w.name or w.id}: {total:g}h < mín {wmin:g}h", worker=w.id))
        if wmax is not None and total > wmax:
            issues.append(ComplianceIssue(rule.id, f"{w.name or w.id}: {total:g}h > máx {wmax:g}h", worker=w.id))
    return issues


@_check("max_shifts_of_type")
def _c_max_type(ctx, rule):
    codes = set(ctx.expand_codes(rule.params.get("shifts"), rule.params.get("period")))
    hi, lo = rule.params.get("max"), rule.params.get("min")
    issues = []
    for w in ctx.workers_in_scope(rule):
        cnt = sum(1 for d in range(ctx.p.horizon_days) if ctx.code_at(w.id, d) in codes)
        if hi is not None and cnt > hi:
            issues.append(ComplianceIssue(rule.id, f"{w.name or w.id}: {cnt} «{'+'.join(codes)}» (máx {hi})", worker=w.id))
        if lo is not None and cnt < lo:
            issues.append(ComplianceIssue(rule.id, f"{w.name or w.id}: {cnt} «{'+'.join(codes)}» (mín {lo})", worker=w.id))
    return issues


@_check("no_mixed_weekends")
def _c_no_mixed(ctx, rule):
    pairs = ctx.weekend_pairs(tuple(rule.params.get("weekend_dows", (5, 6))))
    issues = []
    for w in ctx.workers_in_scope(rule):
        for (s, u) in pairs:
            if ctx.is_work(ctx.code_at(w.id, s)) != ctx.is_work(ctx.code_at(w.id, u)):
                issues.append(ComplianceIssue(rule.id, f"{w.name or w.id}: finde mixto (día {s+1}/{u+1})", worker=w.id, day=s))
    return issues


@_check("same_shift_forbidden")
def _c_same_forbidden(ctx, rule):
    pairs = rule.params.get("pairs", [])
    codes = ctx.expand_codes(rule.params.get("shifts")) or [s.code for s in ctx.p.shifts if s.is_work]
    issues = []
    for (a, b) in pairs:
        for d in range(ctx.p.horizon_days):
            for c in codes:
                if ctx.code_at(a, d) == c and ctx.code_at(b, d) == c:
                    issues.append(ComplianceIssue(rule.id, f"{a} y {b} juntos en «{c}» día {d+1}", day=d))
    return issues


# --------------------------------------------------------------------------- #
# Punto de entrada
# --------------------------------------------------------------------------- #
def audit_compliance(problem: Problem, schedule: dict,
                     rules: Optional[list[Rule]] = None) -> ComplianceReport:
    rules = rules if rules is not None else problem.rules
    ctx = _Ctx(problem, schedule)
    checks, hard_fail = [], False
    for rule in rules:
        fn = CHECKERS.get(rule.type)
        is_hard = rule.mode == RuleMode.HARD
        if fn is None:
            checks.append(RuleCheck(rule.id, rule.type, rule.mode.value,
                                    "unchecked", [], rule.citation))
            continue
        issues = fn(ctx, rule)
        if issues:
            status = "fail" if is_hard else "advisory"
            if is_hard:
                hard_fail = True
        else:
            status = "pass"
        checks.append(RuleCheck(rule.id, rule.type, rule.mode.value, status,
                                issues, rule.citation))
    hard = [c for c in checks if c.mode == "hard"]
    summary = {
        "rules_checked": len([c for c in checks if c.status != "unchecked"]),
        "hard_rules": len(hard),
        "hard_failed": len([c for c in hard if c.status == "fail"]),
        "total_issues": sum(len(c.issues) for c in checks),
        "advisories": len([c for c in checks if c.status == "advisory"]),
    }
    return ComplianceReport(compliant=not hard_fail, checks=checks, summary=summary)


# --------------------------------------------------------------------------- #
# Convenio de ejemplo ("convenio as code") — Estatuto Marco / hospital
# --------------------------------------------------------------------------- #
def estatuto_marco_rules(coverage: Optional[dict] = None) -> list[Rule]:
    """Conjunto base de reglas con cita legal, como ejemplo de 'convenio as code'."""
    rules = [
        Rule("min_rest_hours_between_shifts", mode="hard", tier=3,
             id="descanso 12h", params={"min_hours": 12},
             citation={"ref": "Art. 8 RD 1146/2006 / Estatuto Marco",
                       "note": "12h de descanso entre jornadas"}),
        Rule("max_consecutive_work_days", mode="hard", tier=3,
             id="máx días seguidos", params={"max": 7},
             citation={"ref": "Estatuto Marco art. 52",
                       "note": "descanso semanal: no más de 7 días seguidos"}),
        Rule("max_consecutive_shift", mode="hard", tier=2,
             id="máx noches seguidas", params={"shifts": ["N"], "max": 5},
             citation={"ref": "Convenio — límite de noches consecutivas"}),
        Rule("forbidden_sequence", mode="hard", tier=3,
             id="no mañana tras noche", params={"from": ["N"], "to_period": "morning"},
             citation={"ref": "Descanso entre turnos",
                       "note": "no encadenar noche con mañana"}),
        Rule("min_free_weekends", mode="soft", weight=6, tier=1,
             id="findes libres/mes", params={"min": 1},
             citation={"ref": "Convenio — conciliación", "note": "≥1 finde libre/mes"}),
    ]
    if coverage:
        rules.insert(0, Rule("coverage", mode="hard", tier=3, id="cobertura mínima",
                             params=coverage,
                             citation={"ref": "Plan funcional de la unidad",
                                       "note": "dotación mínima por turno"}))
    return rules
