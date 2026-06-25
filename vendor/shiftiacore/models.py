"""
ShiftiaCoreV8 — modelos de dominio (el molde).

Todo es genérico y data-driven: no hay nada de un cliente concreto aquí.
Cada cliente futuro ("guante") se describe construyendo un `Problem` con sus
turnos, trabajadores y reglas. El motor (engine.py) traduce eso a un modelo de
optimización CP-SAT y devuelve una `Solution`.

Diseño:
  - ShiftType : un tipo de turno (M, T, N, TP, OFF, ...). Data, no código.
  - Worker    : una persona, con turnos permitidos, horas de contrato,
                preasignaciones (fijos), indisponibilidades y preferencias.
  - DayInfo   : metadatos de un día del horizonte (finde, festivo, etiquetas).
  - Rule      : una regla DURA o BLANDA, con peso y "tier" (prioridad
                lexicográfica). El tipo de regla se resuelve contra el
                registro de reglas (rules.py).
  - Problem   : el problema completo a resolver.
  - Solution  : el resultado: planilla, violaciones, estado y estadísticas.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class RuleMode(str, Enum):
    """Una regla se aplica como restricción dura o como penalización blanda."""
    HARD = "hard"
    SOFT = "soft"


def _to_minutes(hhmm: Optional[str]) -> Optional[int]:
    """'HH:MM' -> minutos desde medianoche. None si no se da."""
    if hhmm is None:
        return None
    h, m = str(hhmm).split(":")
    return int(h) * 60 + int(m)


@dataclass
class ShiftType:
    """Un tipo de turno. `is_work=False` marca descanso/libranza."""
    code: str
    label: str = ""
    hours: float = 0.0
    is_work: bool = True
    is_rest: bool = False
    # Periodo del día — habilita reglas de secuencia/transición legibles
    # ("ningún turno de mañana tras una noche"). Valores libres, p.ej.
    # "morning" | "afternoon" | "night".
    period: Optional[str] = None
    # Hora de inicio/fin "HH:MM" (opcional). Habilita la regla de descanso
    # mínimo por HORAS reales entre turnos. Si fin <= inicio, cruza medianoche.
    start: Optional[str] = None
    end: Optional[str] = None
    # Turno PARTIDO: varios tramos en el mismo día, p.ej.
    # [{"start":"09:00","end":"13:00"}, {"start":"17:00","end":"21:00"}].
    # Sigue siendo UN estado asignable (no dos turnos): respeta un-turno/día.
    segments: list[dict] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def __post_init__(self):
        # horas efectivas = suma de tramos si es partido y no se dieron horas
        if not self.hours and self.segments:
            self.hours = round(sum((e - s) for (s, e) in self.intervals()) / 60, 4)

    def start_min(self) -> Optional[int]:
        return _to_minutes(self.start)

    def end_min(self) -> Optional[int]:
        """Minutos de fin; si cruza medianoche, > 1440."""
        s, e = _to_minutes(self.start), _to_minutes(self.end)
        if s is None or e is None:
            return None
        return e + 1440 if e <= s else e

    def intervals(self) -> list[tuple[int, int]]:
        """Tramos activos en minutos [(inicio, fin)]; fin>1440 si cruza medianoche."""
        if self.segments:
            out = []
            for seg in self.segments:
                s, e = _to_minutes(seg["start"]), _to_minutes(seg["end"])
                if s is None or e is None:
                    continue
                out.append((s, e + 1440 if e <= s else e))
            return out
        s, e = self.start_min(), self.end_min()
        return [(s, e)] if (s is not None and e is not None) else []

    def first_start_min(self) -> Optional[int]:
        iv = self.intervals()
        return min(s for s, _ in iv) if iv else None

    def last_end_min(self) -> Optional[int]:
        iv = self.intervals()
        return max(e for _, e in iv) if iv else None


@dataclass
class Preference:
    """Preferencia blanda de una persona. weight>0 prefiere, weight<0 evita."""
    day: int
    shift: str
    weight: int = 1


@dataclass
class Worker:
    """Una persona a planificar."""
    id: str
    name: str = ""
    # Códigos de turno productivo que esta persona PUEDE hacer.
    # None = todos los turnos de trabajo definidos en el problema.
    allowed_shifts: Optional[list[str]] = None
    skills: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)
    # Objetivo / límites de horas en el horizonte (blando salvo que una regla
    # contract_hours lo declare duro).
    contract_hours: Optional[float] = None
    min_hours: Optional[float] = None
    max_hours: Optional[float] = None
    # Preasignaciones fijas (DURO): {indice_dia: codigo_turno}. Se respetan sí o
    # sí (vacaciones, bajas, turnos bloqueados a mano por el gestor).
    fixed: dict[int, str] = field(default_factory=dict)
    # Indisponibilidades (DURO): {indice_dia: [codigos]} — no puede hacer esos
    # turnos ese día. Usa "*" para bloquear el día entero (solo descanso).
    unavailable: dict[int, list[str]] = field(default_factory=dict)
    # Preferencias (BLANDO): las consume la regla 'preferences'.
    preferences: list[Preference] = field(default_factory=list)
    # Horizonte rodante: turnos de los días INMEDIATAMENTE anteriores al
    # horizonte (más antiguo primero, más reciente último). Permite que rachas,
    # noches seguidas y secuencias prohibidas crucen el límite de mes.
    history: list[str] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class DayInfo:
    """Metadatos de un día del horizonte (índice 0..H-1)."""
    index: int
    date: Optional[str] = None      # ISO "YYYY-MM-DD" (opcional)
    dow: Optional[int] = None       # 0=lunes .. 6=domingo
    is_weekend: bool = False
    is_holiday: bool = False
    tags: list[str] = field(default_factory=list)


@dataclass
class Rule:
    """
    Una regla del molde. `type` apunta a una regla registrada en rules.py.

    mode   : HARD (restricción) o SOFT (penalización en el objetivo).
    weight : coste por unidad de violación (solo SOFT).
    tier   : prioridad lexicográfica. Un tier más alto SIEMPRE domina a todos
             los tiers inferiores combinados (igual que el orden lexicográfico
             del motor antiguo, pero garantizado por el solver).
    params : parámetros específicos de la regla.
    scope  : filtro opcional {"workers":[ids], "groups":[g], "skills":[s],
             "day_tags":[t]} para aplicar la regla solo a un subconjunto.
    id     : identificador legible para el reporte de violaciones.
    """
    type: str
    mode: RuleMode = RuleMode.HARD
    weight: int = 1
    tier: int = 0
    params: dict[str, Any] = field(default_factory=dict)
    scope: dict[str, Any] = field(default_factory=dict)
    id: Optional[str] = None
    # Referencia legal/convenio (opcional): {"ref": "Art. 14 Estatuto Marco",
    # "note": "descanso mínimo 12h"}. La usa el auditor (compliance.py) para
    # citar la fuente de cada incumplimiento. "Convenio as code".
    citation: Optional[dict] = None

    def __post_init__(self):
        if isinstance(self.mode, str):
            self.mode = RuleMode(self.mode)
        if self.id is None:
            self.id = self.type


@dataclass
class Problem:
    """El problema completo a resolver."""
    horizon_days: int
    shifts: list[ShiftType]
    workers: list[Worker]
    days: list[DayInfo] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    rest_code: str = "OFF"          # turno de descanso implícito si no se define
    meta: dict[str, Any] = field(default_factory=dict)

    # ---- helpers ----
    def shift(self, code: str) -> Optional[ShiftType]:
        for s in self.shifts:
            if s.code == code:
                return s
        return None

    def work_shift_codes(self) -> list[str]:
        return [s.code for s in self.shifts if s.is_work]

    def rest_shift(self) -> ShiftType:
        s = self.shift(self.rest_code)
        if s is None:
            s = ShiftType(code=self.rest_code, label="Descanso",
                          hours=0.0, is_work=False, is_rest=True)
        return s

    def allowed_for(self, worker: Worker) -> list[str]:
        work = self.work_shift_codes()
        if worker.allowed_shifts is not None:
            allow = set(worker.allowed_shifts)
            work = [c for c in work if c in allow]
        return work

    def day(self, index: int) -> DayInfo:
        for d in self.days:
            if d.index == index:
                return d
        return DayInfo(index=index)


@dataclass
class Violation:
    """Una regla blanda que no se cumplió del todo (o una dura relajada)."""
    rule_id: str
    rule_type: str
    tier: int
    amount: int          # unidades de violación
    weight: int          # peso base de la regla
    cost: int            # coste efectivo en el objetivo
    detail: str = ""


@dataclass
class Relaxation:
    """Una regla DURA que hubo que relajar para poder dar una planilla."""
    rule_id: str
    rule_type: str
    tier: int
    detail: str = ""


@dataclass
class Solution:
    status: str                              # OPTIMAL | FEASIBLE | INFEASIBLE | ...
    feasible: bool
    schedule: dict[str, dict[int, str]]      # {worker_id: {day_index: shift_code}}
    objective: int
    violations: list[Violation]
    stats: dict[str, Any]
    justification: list[str] = field(default_factory=list)
    # Métricas/KPIs de la planilla (por trabajador y globales).
    metrics: dict[str, Any] = field(default_factory=dict)
    # Si INFEASIBLE: ids de las reglas duras en conflicto mínimo.
    conflict: list[str] = field(default_factory=list)
    # Si se usó auto-relajación: qué reglas duras se relajaron.
    relaxations: list[Relaxation] = field(default_factory=list)
    # Validación de entrada: errores (bloqueantes) y avisos (no bloqueantes).
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
