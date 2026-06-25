"""
ShiftiaCoreV8 — previsión de demanda.

Convierte el HISTÓRICO de un cliente (cuánta gente hizo falta por turno y día)
en la cobertura para el próximo horizonte, que alimenta la regla `coverage`.

Por qué importa: una planilla perfectamente óptima contra una demanda mal puesta
sigue estando mal. Esta es la mayor palanca de CERTEZA del producto.

Método (sin dependencias pesadas, determinista y explicable):
  - Agrupa el histórico por (tipo de día, turno): laborable / finde / festivo.
  - Pondera por recencia (vida media configurable): las semanas recientes pesan más.
  - Toma un CUANTIL de nivel de servicio (p.ej. 0.8) para no quedarte corto:
    cubres lo que pasó el 80% de los días, no la media.
  - Proyecta sobre las fechas del horizonte (con su dow/festivo).

Es estadística honesta. Para producción se puede cambiar el estimador por un
modelo ML (LightGBM/Prophet) sin tocar el resto: la salida es la misma.
"""
from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass, field
from typing import Optional

from .models import Rule


def _daytype(d: _dt.date, holidays: set) -> str:
    if d.isoformat() in holidays:
        return "holiday"
    return "weekend" if d.weekday() >= 5 else "weekday"


def _weighted_quantile(pairs: list[tuple[float, float]], q: float) -> float:
    """pairs = [(valor, peso)]. Cuantil ponderado q in [0,1]."""
    if not pairs:
        return 0.0
    pairs = sorted(pairs, key=lambda p: p[0])
    total = sum(w for _, w in pairs)
    if total <= 0:
        return pairs[-1][0]
    thresh = q * total
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= thresh:
            return v
    return pairs[-1][0]


@dataclass
class DemandForecast:
    by_day: dict[int, dict[str, dict]] = field(default_factory=dict)
    by_daytype: dict[str, dict[str, dict]] = field(default_factory=dict)
    explanation: list[str] = field(default_factory=list)

    def to_coverage_rule(self, mode: str = "hard", tier: int = 3,
                         id: str = "cobertura", use: str = "by_day") -> Rule:
        if use == "by_day" and self.by_day:
            params = {"by_day": {str(k): v for k, v in self.by_day.items()}}
        else:
            params = {"by_daytype": self.by_daytype}
        return Rule("coverage", mode=mode, tier=tier, id=id, params=params)


def forecast_demand(history: list[dict], *, horizon_days: int,
                    start_date: Optional[str] = None,
                    holidays: Optional[list] = None,
                    service_level: float = 0.8,
                    max_quantile: Optional[float] = 0.95,
                    half_life_days: int = 28,
                    min_floor: int = 0,
                    load_per_staff: float = 1.0) -> DemandForecast:
    """
    history: lista de {"date":"YYYY-MM-DD","shift":code,"value":num}
             value = personal necesario observado (o carga si usas load_per_staff).
    Devuelve un DemandForecast con by_day (si hay start_date) y by_daytype.
    """
    holidays = set(str(h) for h in (holidays or []))
    if not history:
        return DemandForecast(explanation=["Sin histórico: no se puede prever."])

    # fecha de referencia para la recencia = la más reciente del histórico
    dates = [_dt.date.fromisoformat(r["date"]) for r in history]
    ref = max(dates)
    shifts = sorted({r["shift"] for r in history})

    # buckets[(daytype, shift)] = [(valor_en_staff, peso)]
    buckets: dict[tuple, list] = {}
    for r in history:
        d = _dt.date.fromisoformat(r["date"])
        staff = float(r["value"]) / load_per_staff
        age = (ref - d).days
        w = 0.5 ** (age / half_life_days) if half_life_days > 0 else 1.0
        buckets.setdefault((_daytype(d, holidays), r["shift"]), []).append((staff, w))

    def demand_for(daytype: str) -> dict:
        out = {}
        for s in shifts:
            pts = buckets.get((daytype, s)) or buckets.get(("weekday", s)) or []
            lo = math.ceil(_weighted_quantile(pts, service_level)) if pts else 0
            lo = max(lo, min_floor)
            spec = {"min": lo}
            if max_quantile is not None and pts:
                hi = math.ceil(_weighted_quantile(pts, max_quantile))
                if hi >= lo:
                    spec["max"] = hi
            out[s] = spec
        return out

    by_daytype = {dt: demand_for(dt) for dt in ("weekday", "weekend", "holiday")}

    by_day = {}
    if start_date:
        sd = _dt.date.fromisoformat(start_date)
        for i in range(horizon_days):
            day = sd + _dt.timedelta(days=i)
            by_day[i] = demand_for(_daytype(day, holidays))

    exp = [f"{len(history)} registros, {len(shifts)} turnos; "
           f"nivel de servicio {int(service_level*100)}%, "
           f"vida media {half_life_days}d (recencia)."]
    for dt in ("weekday", "weekend", "holiday"):
        n = sum(len(buckets.get((dt, s), [])) for s in shifts)
        if n:
            dem = ", ".join(f"{s}:{by_daytype[dt][s]['min']}" for s in shifts)
            exp.append(f"{dt}: {dem} (de {n} obs).")
    return DemandForecast(by_day=by_day, by_daytype=by_daytype, explanation=exp)


@dataclass
class TimeDemandForecast:
    by_daytype: dict[str, list] = field(default_factory=dict)   # {weekday:[bands], ...}
    explanation: list[str] = field(default_factory=list)

    def to_time_coverage_rule(self, mode: str = "hard", tier: int = 3,
                              id: str = "cobertura por franja") -> Rule:
        return Rule("time_coverage", mode=mode, tier=tier, id=id,
                    params={"by_daytype": self.by_daytype})


def forecast_time_demand(history: list[dict], *,
                         holidays: Optional[list] = None,
                         service_level: float = 0.8,
                         half_life_days: int = 28,
                         load_per_staff: float = 1.0,
                         open_hour: int = 0, close_hour: int = 24,
                         min_staff: int = 0) -> TimeDemandForecast:
    """
    history: lista de {"date","hour",value} o {"date","from","to","value"}.
      value = carga/afluencia (o personal necesario si load_per_staff=1).
    Devuelve dotación por FRANJA y tipo de día, lista para time_coverage.
    """
    holidays = set(str(h) for h in (holidays or []))
    if not history:
        return TimeDemandForecast(explanation=["Sin histórico de afluencia."])

    def hours_of(rec):
        if "hour" in rec:
            return [int(rec["hour"])]
        f = int(str(rec["from"]).split(":")[0])
        t = int(str(rec["to"]).split(":")[0])
        return list(range(f, t))

    dates = [_dt.date.fromisoformat(r["date"]) for r in history]
    ref = max(dates)
    buckets: dict[tuple, list] = {}   # (daytype, hour) -> [(staff, peso)]
    for r in history:
        d = _dt.date.fromisoformat(r["date"])
        dtp = _daytype(d, holidays)
        staff = float(r["value"]) / load_per_staff
        w = 0.5 ** ((ref - d).days / half_life_days) if half_life_days > 0 else 1.0
        for h in hours_of(r):
            buckets.setdefault((dtp, h), []).append((staff, w))

    by_daytype = {}
    for dtp in ("weekday", "weekend", "holiday"):
        req = {}
        for h in range(open_hour, close_hour):
            pts = buckets.get((dtp, h)) or buckets.get(("weekday", h)) or []
            req[h] = max(min_staff, math.ceil(_weighted_quantile(pts, service_level))) if pts else min_staff
        # fusiona horas consecutivas con igual requisito (>0) en bandas
        bands, h = [], open_hour
        while h < close_hour:
            need = req[h]
            if need <= 0:
                h += 1
                continue
            j = h
            while j < close_hour and req[j] == need:
                j += 1
            bands.append({"from": f"{h:02d}:00", "to": f"{j:02d}:00", "min": need})
            h = j
        by_daytype[dtp] = bands

    exp = [f"{len(history)} registros; nivel de servicio {int(service_level*100)}%."]
    for dtp in ("weekday", "weekend", "holiday"):
        if by_daytype[dtp]:
            exp.append(f"{dtp}: " + ", ".join(f"{b['from']}-{b['to']}×{b['min']}"
                                              for b in by_daytype[dtp]))
    return TimeDemandForecast(by_daytype=by_daytype, explanation=exp)


def synthesize_footfall(weeks: int = 8, start_date: str = "2026-03-01",
                        open_hour: int = 9, close_hour: int = 21,
                        load_per_staff: float = 40.0, seed: int = 0) -> list[dict]:
    """Histórico sintético de afluencia por hora (picos comida/tarde) para demos."""
    import random
    rng = random.Random(seed)
    sd = _dt.date.fromisoformat(start_date)
    # perfil relativo de afluencia por hora (0 fuera de apertura)
    prof = {h: 0.0 for h in range(24)}
    for h in range(open_hour, close_hour):
        base = 1.0
        if 12 <= h < 16:
            base = 2.2          # pico comida
        elif 18 <= h < 21:
            base = 2.0          # pico tarde
        prof[h] = base
    out = []
    for d in range(weeks * 7):
        day = sd + _dt.timedelta(days=d)
        wf = 1.3 if day.weekday() >= 5 else 1.0
        for h in range(open_hour, close_hour):
            load = prof[h] * load_per_staff * wf * (1 + rng.uniform(-0.1, 0.1))
            out.append({"date": day.isoformat(), "hour": h, "value": round(load)})
    return out


def synthesize_history(weeks: int = 12, start_date: str = "2026-03-01",
                       base: Optional[dict] = None, weekend_factor: float = 0.6,
                       noise: int = 1, seed: int = 0) -> list[dict]:
    """
    Genera un histórico sintético plausible (para demos/tests): M/T/N con más
    carga entre semana y menos en finde, más algo de ruido.
    """
    import random
    rng = random.Random(seed)
    base = base or {"M": 3, "T": 3, "N": 2}
    sd = _dt.date.fromisoformat(start_date)
    hist = []
    for d in range(weeks * 7):
        day = sd + _dt.timedelta(days=d)
        we = day.weekday() >= 5
        for s, b in base.items():
            val = b * (weekend_factor if we else 1.0)
            val = max(0, round(val) + rng.randint(-noise, noise))
            hist.append({"date": day.isoformat(), "shift": s, "value": val})
    return hist
