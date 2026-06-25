"""
ShiftiaCoreV8 — lenguaje natural → reglas.

Convierte instrucciones en español ("deja a Ana libre los viernes", "máximo 2
noches a Marco", "cubrir 3 personas por turno", "12 horas de descanso") en
reglas del molde. Baja la barrera de configurar cada cliente ("guante").

Dos vías:
  - translate(text, problem, llm=fn): si le pasas una función LLM, usa el prompt
    estructurado (build_prompt) y valida su JSON (parse_llm_json). Para producción.
  - interpret(text, problem): intérprete HEURÍSTICO sin dependencias ni red, que
    ya entiende las frases más comunes. Funciona hoy, offline, y es la red de
    seguridad si no hay LLM configurado.

El LLM nunca crea la planilla: solo traduce intención a reglas, que el solver
(determinista) aplica. Así no hay alucinaciones en el cuadrante.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Optional

from .models import Problem, Rule
from .rules import RULES_REGISTRY

_DOWS = {"lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3, "viernes": 4,
         "sabado": 5, "domingo": 6}
_NUMS = {"un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4,
         "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
         "once": 11, "doce": 12}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


@dataclass
class NLResult:
    rules: list[Rule] = field(default_factory=list)
    worker_unavailable: dict[str, dict[int, list]] = field(default_factory=dict)
    worker_allowed: dict[str, list] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)

    def apply(self, problem: Problem) -> Problem:
        """Aplica el resultado al problema (añade reglas, parchea trabajadores)."""
        problem.rules.extend(self.rules)
        byid = {w.id: w for w in problem.workers}
        for wid, days in self.worker_unavailable.items():
            w = byid.get(wid)
            if w:
                for d, codes in days.items():
                    w.unavailable[int(d)] = codes
        for wid, allowed in self.worker_allowed.items():
            w = byid.get(wid)
            if w:
                w.allowed_shifts = allowed
        return problem


# --------------------------------------------------------------------------- #
# Helpers de dominio
# --------------------------------------------------------------------------- #
def _period_codes(problem, period):
    codes = [s.code for s in problem.shifts if s.period == period]
    if codes:
        return codes
    fallback = {"morning": "M", "afternoon": "T", "night": "N"}.get(period)
    return [fallback] if fallback and problem.shift(fallback) else []


def _find_number(text):
    m = re.search(r"\b(\d+)\b", text)
    if m:
        return int(m.group(1))
    for w, n in _NUMS.items():
        if re.search(rf"\b{w}\b", text):
            return n
    return None


def _find_dows(text):
    dows = []
    for name, idx in _DOWS.items():
        if re.search(rf"\b{name}s?\b", text):
            dows.append(idx)
    if re.search(r"\bfinde?s?\b", text) or "fin de semana" in text:
        dows += [5, 6]
    return sorted(set(dows))


def _find_workers(text, problem):
    found = []
    for w in problem.workers:
        nm = _norm(w.name or "")
        if nm and re.search(rf"\b{re.escape(nm)}\b", text):
            found.append(w)
    return found


def _horizon_days_for_dows(problem, dows):
    """Índices del horizonte cuyo día de la semana está en dows."""
    import datetime as dt
    start = problem.meta.get("start_date")
    if not start or not dows:
        return None
    sd = dt.date.fromisoformat(start)
    out = []
    for i in range(problem.horizon_days):
        if (sd + dt.timedelta(days=i)).weekday() in dows:
            out.append(i)
    return out


# --------------------------------------------------------------------------- #
# Intérprete heurístico (offline)
# --------------------------------------------------------------------------- #
def interpret(text: str, problem: Problem) -> NLResult:
    res = NLResult()
    raw_clauses = re.split(r"[;\n.,]| y (?=\w)", text)
    for raw in raw_clauses:
        clause = _norm(raw).strip()
        if not clause:
            continue
        ws = _find_workers(clause, problem)
        dows = _find_dows(clause)
        num = _find_number(clause)
        w0 = ws[0] if ws else None
        wid = w0.id if w0 else None
        handled = False

        # 1) "X libre los viernes" / "X no trabaja los findes"
        if w0 and dows and re.search(r"\blibre|descansa|no trabaja|no curra|fiesta\b", clause):
            idxs = _horizon_days_for_dows(problem, dows)
            if idxs is None:
                res.notes.append(f"'{raw.strip()}': necesito start_date para mapear "
                                 f"los días de la semana; omitido.")
            else:
                res.worker_unavailable.setdefault(wid, {})
                for i in idxs:
                    res.worker_unavailable[wid][i] = ["*"]
                res.notes.append(f"{w0.name}: libre {len(idxs)} día(s) "
                                 f"({', '.join(k for k,v in _DOWS.items() if v in dows)}).")
            handled = True

        # 2) "X no hace noches" / "X solo mañanas"
        elif w0 and re.search(r"\bno\b.*\bnoches?\b", clause):
            work = [s.code for s in problem.shifts if s.is_work]
            night = set(_period_codes(problem, "night"))
            res.worker_allowed[wid] = [c for c in work if c not in night]
            res.notes.append(f"{w0.name}: sin noches.")
            handled = True
        elif w0 and re.search(r"\bsolo\b|\bunicamente\b", clause) and \
                re.search(r"\bmanana|tarde|noche\b", clause):
            per = ("morning" if "manana" in clause else
                   "afternoon" if "tarde" in clause else "night")
            res.worker_allowed[wid] = _period_codes(problem, per)
            res.notes.append(f"{w0.name}: solo {per}.")
            handled = True

        # 3) "máximo N noches a Marco"
        elif w0 and num is not None and "noche" in clause and \
                re.search(r"\bmax|maximo|no mas de|como mucho\b", clause):
            res.rules.append(Rule("max_shifts_of_type", mode="hard", tier=2,
                                  id=f"máx {num} noches {w0.name}",
                                  params={"shifts": _period_codes(problem, "night"),
                                          "max": num}, scope={"workers": [wid]}))
            res.notes.append(f"{w0.name}: máx {num} noches.")
            handled = True

        # 4) "12 horas de descanso entre turnos"
        elif num is not None and "hora" in clause and "descanso" in clause:
            res.rules.append(Rule("min_rest_hours_between_shifts", mode="hard",
                                  tier=3, id=f"descanso {num}h",
                                  params={"min_hours": num}))
            res.notes.append(f"Descanso mínimo {num}h entre turnos.")
            handled = True

        # 5) "nadie más de N días seguidos"
        elif num is not None and re.search(r"dias? seguidos|consecutiv", clause):
            res.rules.append(Rule("max_consecutive_work_days", mode="hard", tier=3,
                                  id=f"máx {num} días seguidos", params={"max": num}))
            res.notes.append(f"Máx {num} días seguidos.")
            handled = True

        # 6) "nada de mañana tras noche"
        elif re.search(r"manana", clause) and re.search(r"tras|despues de|luego de", clause) \
                and "noche" in clause:
            res.rules.append(Rule("forbidden_sequence", mode="hard", tier=3,
                                  id="no mañana tras noche",
                                  params={"from": _period_codes(problem, "night"),
                                          "to": _period_codes(problem, "morning")}))
            res.notes.append("Prohibido mañana tras noche.")
            handled = True

        # 7) "cubrir N personas por turno" (o por un turno concreto)
        elif num is not None and re.search(r"cubrir|cubre|necesito|necesita|minimo|hacen falta|al menos", clause) \
                and re.search(r"persona|gente|turno|trabajador|manana|tarde|noche", clause):
            per = ("night" if "noche" in clause else "morning" if "manana" in clause
                   else "afternoon" if "tarde" in clause else None)
            if per:
                codes = _period_codes(problem, per)
                demand = {c: {"min": num} for c in codes}
            else:
                demand = {s.code: {"min": num} for s in problem.shifts if s.is_work}
            res.rules.append(Rule("coverage", mode="hard", tier=3,
                                  id="cobertura (texto)", params={"demand": demand}))
            res.notes.append(f"Cobertura mínima {num} por turno"
                             + (f" ({per})" if per else "") + ".")
            handled = True

        # 8) "al menos N findes libres"
        elif num is not None and re.search(r"finde?s?|fin de semana", clause) \
                and "libre" in clause:
            res.rules.append(Rule("min_free_weekends", mode="soft", weight=6, tier=2,
                                  id=f"{num} findes libres", params={"min": num}))
            res.notes.append(f"Al menos {num} finde(s) libre(s).")
            handled = True

        if not handled:
            res.unmatched.append(raw.strip())
    return res


# --------------------------------------------------------------------------- #
# Vía LLM (producción)
# --------------------------------------------------------------------------- #
def build_prompt(problem: Problem, text: str) -> str:
    """Construye el prompt para un LLM: que devuelva SOLO JSON con reglas válidas."""
    names = ", ".join(f"{w.id}={w.name or w.id}" for w in problem.workers)
    shifts = ", ".join(f"{s.code}({s.period or 'sin periodo'})" for s in problem.shifts)
    return f"""Eres un traductor de reglas para un planificador de turnos.
Convierte la instrucción del usuario en JSON ESTRICTO (sin texto adicional) con la forma:
{{"rules":[{{"type":..., "mode":"hard|soft", "tier":int, "weight":int, "params":{{}}, "scope":{{}}, "id":"texto"}}],
  "worker_unavailable":{{"<worker_id>":{{"<dia>":["*"]}}}},
  "worker_allowed":{{"<worker_id>":["M","T"]}},
  "notes":["..."]}}

Tipos de regla válidos: {', '.join(sorted(RULES_REGISTRY))}.
Turnos disponibles: {shifts}.
Trabajadores: {names}.
Horizonte: {problem.horizon_days} días, start_date={problem.meta.get('start_date')}.

Reglas: lo innegociable -> mode "hard"; lo deseable -> mode "soft" con weight; el
orden de prioridad del negocio -> tier (mayor = más prioritario).

Instrucción del usuario:
\"\"\"{text}\"\"\"
"""


def parse_llm_json(raw: str, problem: Problem) -> NLResult:
    """Valida y parsea el JSON devuelto por un LLM a un NLResult."""
    res = NLResult()
    txt = raw.strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```[a-z]*\n?|```$", "", txt).strip()
    try:
        data = json.loads(txt)
    except Exception as e:
        res.notes.append(f"JSON del LLM no parseable: {e}")
        return res
    for rd in data.get("rules", []):
        t = rd.get("type")
        if t not in RULES_REGISTRY:
            res.notes.append(f"Regla '{t}' del LLM ignorada (no existe).")
            continue
        res.rules.append(Rule(type=t, mode=rd.get("mode", "hard"),
                              weight=int(rd.get("weight", 1)),
                              tier=int(rd.get("tier", 0)),
                              params=rd.get("params", {}) or {},
                              scope=rd.get("scope", {}) or {}, id=rd.get("id")))
    for wid, days in (data.get("worker_unavailable") or {}).items():
        res.worker_unavailable[wid] = {int(d): c for d, c in days.items()}
    for wid, allowed in (data.get("worker_allowed") or {}).items():
        res.worker_allowed[wid] = allowed
    res.notes.extend(data.get("notes", []))
    return res


def translate(text: str, problem: Problem,
              llm: Optional[Callable[[str], str]] = None) -> NLResult:
    """
    Traduce texto a reglas. Si `llm` (función str->str) está disponible, usa el
    prompt estructurado + validación; si no, el intérprete heurístico offline.
    """
    if llm is not None:
        try:
            return parse_llm_json(llm(build_prompt(problem, text)), problem)
        except Exception as e:
            r = interpret(text, problem)
            r.notes.append(f"LLM falló ({e}); usado intérprete heurístico.")
            return r
    return interpret(text, problem)
