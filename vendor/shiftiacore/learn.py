"""
ShiftiaCoreV8 — aprendizaje de las ediciones (el foso).

Cada vez que la supervisora corrige la planilla (el motor propuso X, ella dejó
Y), eso es una ETIQUETA de una regla no escrita que el motor no conocía. Este
módulo mira esas correcciones y propone reglas/preferencias para que ella las
confirme una vez — y el motor deje de fallar ahí. El producto mejora con el uso.

Es estadístico y EXPLICABLE (soporte + confianza), no una caja negra:
cada sugerencia dice cuántas veces pasó y con qué consistencia. La supervisora
siempre confirma; nunca se aplica nada solo.

Inferencias (v1):
  1. Evitar/limitar un turno por persona     → allowed_shifts | max_shifts_of_type
  2. Libranza recurrente por día de semana    → indisponibilidad ese dow
  3. Secuencia prohibida global (p.ej. N→M)   → forbidden_sequence
  4. Deriva de cobertura (sobra/falta gente)  → ajuste de coverage (informativo)

Entrada: episodios (propuesto vs final por trabajador y mes).
Salida: lista de Suggestion, cada una aplicable con apply_suggestions().
"""
from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass, field
from typing import Any, Optional

from .models import Problem, Rule, ShiftType, Worker


@dataclass
class EditEpisode:
    """Un mes de un trabajador: lo que el motor propuso vs lo que quedó."""
    worker_id: str
    year: int
    month: int                 # 0-based, como el resto del sistema
    proposed: list[str]        # cells[0..30] del motor/Shiftia
    final: list[str]           # cells[0..30] que dejó la supervisora
    worker_name: str = ""


@dataclass
class Suggestion:
    id: str
    kind: str                  # avoid | daily_off | forbidden_sequence | coverage | prefer
    scope: str                 # worker_id | "global"
    text: str                  # explicación legible
    support: int               # nº de observaciones que la respaldan
    confidence: float          # 0..1
    rule: Optional[Rule] = None
    worker_patch: dict = field(default_factory=dict)   # {allowed_shifts|avoid_dows|...}
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        r = self.rule
        return {
            "id": self.id, "kind": self.kind, "scope": self.scope,
            "text": self.text, "support": self.support,
            "confidence": round(self.confidence, 3),
            "rule": None if r is None else {
                "type": r.type, "mode": r.mode.value, "tier": r.tier,
                "weight": r.weight, "params": r.params, "scope": r.scope, "id": r.id},
            "worker_patch": self.worker_patch, "data": self.data,
        }


def episode_from_sync(worker_id, year, month, before, after, worker_name="") -> EditEpisode:
    """Construye un episodio desde un sync de la extensión (antes→después)."""
    return EditEpisode(worker_id=worker_id, year=year, month=month,
                       proposed=list(before or []), final=list(after or []),
                       worker_name=worker_name)


def _dow(year, month, day_idx):
    try:
        return _dt.date(year, month + 1, day_idx + 1).weekday()
    except ValueError:
        return None


def learn_from_edits(episodes: list[EditEpisode], shifts: list[ShiftType], *,
                     min_support: int = 3, min_confidence: float = 0.6) -> list[Suggestion]:
    smap = {s.code: s for s in shifts}
    work = {c for c, s in smap.items() if s.is_work}

    def is_work(code):
        s = smap.get(code)
        return bool(s and s.is_work)

    def is_rest(code):
        s = smap.get(code)
        # rest explícito, o cualquier código no-trabajo conocido (OFF/L/D...)
        return bool(s and not s.is_work) or (code == "" )

    out: list[Suggestion] = []
    DOWN = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

    # ---- 1) Evitar/limitar un turno por persona ----
    # por worker: para cada turno S, cuántas veces se propuso y cuántas se quitó.
    perw_prop = {}   # (wid, S) -> veces propuesto
    perw_removed = {}  # (wid, S) -> propuesto S pero final != S
    perw_finalcnt = {}  # (wid, S) -> veces que S quedó en final
    names = {}
    for ep in episodes:
        names[ep.worker_id] = ep.worker_name or ep.worker_id
        n = min(len(ep.proposed), len(ep.final))
        for d in range(n):
            p, f = ep.proposed[d], ep.final[d]
            if p in work:
                perw_prop[(ep.worker_id, p)] = perw_prop.get((ep.worker_id, p), 0) + 1
                if f != p:
                    perw_removed[(ep.worker_id, p)] = perw_removed.get((ep.worker_id, p), 0) + 1
            if f in work:
                perw_finalcnt[(ep.worker_id, f)] = perw_finalcnt.get((ep.worker_id, f), 0) + 1

    for (wid, S), prop in perw_prop.items():
        removed = perw_removed.get((wid, S), 0)
        if prop < min_support:
            continue
        conf = removed / prop
        if conf < min_confidence:
            continue
        nm = names.get(wid, wid)
        finalcnt = perw_finalcnt.get((wid, S), 0)
        if finalcnt == 0:
            # nunca se lo deja → restricción dura de turnos permitidos
            allowed = [c for c in sorted(work) if c != S]
            out.append(Suggestion(
                id=f"avoid:{wid}:{S}", kind="avoid", scope=wid,
                text=f"{nm}: nunca hace «{S}» (se la quitaste {removed}/{prop} veces).",
                support=removed, confidence=conf,
                worker_patch={"allowed_shifts": allowed},
                data={"shift": S}))
        else:
            cap = max(0, math.ceil(finalcnt / max(1, len(episodes))))
            out.append(Suggestion(
                id=f"cap:{wid}:{S}", kind="avoid", scope=wid,
                text=f"{nm}: sueles reducirle «{S}» (la quitaste {removed}/{prop} veces; "
                     f"tope ~{cap}/mes).",
                support=removed, confidence=conf,
                rule=Rule("max_shifts_of_type", mode="soft", weight=4, tier=1,
                          id=f"{nm} máx {S}", params={"shifts": [S], "max": cap},
                          scope={"workers": [wid]}),
                data={"shift": S, "cap": cap}))

    # ---- 2) Libranza recurrente por día de semana ----
    perw_dow_opp = {}   # (wid, dow) -> días laborables propuestos ese dow
    perw_dow_freed = {}  # (wid, dow) -> propuesto trabajo, final descanso, ese dow
    for ep in episodes:
        n = min(len(ep.proposed), len(ep.final))
        for d in range(n):
            dw = _dow(ep.year, ep.month, d)
            if dw is None:
                continue
            if is_work(ep.proposed[d]):
                perw_dow_opp[(ep.worker_id, dw)] = perw_dow_opp.get((ep.worker_id, dw), 0) + 1
                if is_rest(ep.final[d]):
                    perw_dow_freed[(ep.worker_id, dw)] = perw_dow_freed.get((ep.worker_id, dw), 0) + 1
    for (wid, dw), opp in perw_dow_opp.items():
        freed = perw_dow_freed.get((wid, dw), 0)
        if opp < min_support or freed / opp < min_confidence:
            continue
        nm = names.get(wid, wid)
        out.append(Suggestion(
            id=f"off:{wid}:{dw}", kind="daily_off", scope=wid,
            text=f"{nm}: suele librar los {DOWN[dw]} (lo liberaste {freed}/{opp} veces).",
            support=freed, confidence=freed / opp,
            worker_patch={"avoid_dows": [dw]}, data={"dow": dw}))

    # ---- 3) Secuencia prohibida global (p.ej. N→M) ----
    pair_prop = {}   # (A,B) -> adyacencias A->B en propuesto
    pair_final = {}  # (A,B) -> adyacencias A->B en final
    for ep in episodes:
        n = min(len(ep.proposed), len(ep.final))
        for d in range(n - 1):
            a, b = ep.proposed[d], ep.proposed[d + 1]
            if a in work and b in work:
                pair_prop[(a, b)] = pair_prop.get((a, b), 0) + 1
            fa, fb = ep.final[d], ep.final[d + 1]
            if fa in work and fb in work:
                pair_final[(fa, fb)] = pair_final.get((fa, fb), 0) + 1
    for (a, b), prop in pair_prop.items():
        fin = pair_final.get((a, b), 0)
        if prop < min_support:
            continue
        eliminated = prop - fin
        conf = eliminated / prop
        if conf < min_confidence:
            continue
        out.append(Suggestion(
            id=f"seq:{a}->{b}", kind="forbidden_sequence", scope="global",
            text=f"Evitas «{a}» seguido de «{b}» (lo eliminaste {eliminated}/{prop} veces).",
            support=eliminated, confidence=conf,
            rule=Rule("forbidden_sequence", mode="hard", tier=3,
                      id=f"no {a}→{b}", params={"from": [a], "to": [b]}),
            data={"from": a, "to": b}))

    # ---- 4) Deriva de cobertura (informativo) ----
    # agrega por (fecha, turno) entre trabajadores: final - propuesto.
    by_date = {}  # date_iso -> {"prop":{S:n}, "fin":{S:n}, "daytype":...}
    for ep in episodes:
        n = min(len(ep.proposed), len(ep.final))
        for d in range(n):
            dt = _dow(ep.year, ep.month, d)
            if dt is None:
                continue
            iso = _dt.date(ep.year, ep.month + 1, d + 1).isoformat()
            slot = by_date.setdefault(iso, {"prop": {}, "fin": {}, "dow": dt})
            if is_work(ep.proposed[d]):
                slot["prop"][ep.proposed[d]] = slot["prop"].get(ep.proposed[d], 0) + 1
            if is_work(ep.final[d]):
                slot["fin"][ep.final[d]] = slot["fin"].get(ep.final[d], 0) + 1
    drift = {}  # (daytype, S) -> [deltas]
    for iso, slot in by_date.items():
        daytype = "weekend" if slot["dow"] >= 5 else "weekday"
        for S in work:
            delta = slot["fin"].get(S, 0) - slot["prop"].get(S, 0)
            drift.setdefault((daytype, S), []).append(delta)
    for (daytype, S), deltas in drift.items():
        if len(deltas) < min_support:
            continue
        avg = sum(deltas) / len(deltas)
        if abs(avg) < 0.75:   # ruido
            continue
        direction = "más" if avg > 0 else "menos"
        out.append(Suggestion(
            id=f"cov:{daytype}:{S}", kind="coverage", scope="global",
            text=f"Cobertura {daytype} «{S}»: sueles poner {direction} gente "
                 f"({avg:+.1f} de media). Considera ajustar el mínimo.",
            support=len(deltas), confidence=min(1.0, abs(avg)),
            data={"daytype": daytype, "shift": S, "avg_delta": round(avg, 2),
                  "suggest_delta": round(avg)}))

    out.sort(key=lambda s: (-s.confidence, -s.support))
    return out


def apply_suggestions(problem: Problem, suggestions: list[Suggestion],
                      accept: Optional[set] = None) -> Problem:
    """Aplica las sugerencias confirmadas al problema (reglas + parches)."""
    byid = {w.id: w for w in problem.workers}
    # dows del horizonte para 'avoid_dows'
    start = problem.meta.get("start_date")
    sd = _dt.date.fromisoformat(start) if start else None

    for s in suggestions:
        if accept is not None and s.id not in accept:
            continue
        if s.rule is not None:
            problem.rules.append(s.rule)
        w = byid.get(s.scope)
        patch = s.worker_patch or {}
        if w and "allowed_shifts" in patch:
            w.allowed_shifts = patch["allowed_shifts"]
        if w and "avoid_dows" in patch and sd is not None:
            dows = set(patch["avoid_dows"])
            for i in range(problem.horizon_days):
                if (sd + _dt.timedelta(days=i)).weekday() in dows:
                    w.unavailable[i] = ["*"]
    return problem
