"""
Shiftia Diálisis — el "guante": describe la unidad de diálisis (DUE) como DATOS
y se los pasa al motor shiftia-core. Aquí no hay lógica de optimización.

Calibrado con la planilla real "SANITARIO - DIALISIS - DUE" (Octubre 2026):
  MT  = 07:30–19:00 sin pausa (11,5 h)   ← turno presencial de día
  M7H = 07:30–14:30 (7 h) · M = 08:00–15:00 (7 h)   ← supervisora
  D   = Descanso · G17/G24 = guardias localizables (on-call)
Reglas de la unidad: NO se trabaja más de 2 días seguidos (turnos de 12 h).
Domingos: cierra. Noelia Monteserín es la SUPERVISORA (usuaria del sistema).

Objetivo del producto (para la supervisora):
  - ¿puede librar X el día Y?      → can_release
  - ¿es posible este cambio?       → can_swap
  - catástrofe: ¿quién cubre?      → cover_catastrophe
todo respetando convenio, horas y cobertura.
"""
import bootstrap  # noqa: F401  (engancha shiftiacore)

import datetime as dt
from shiftiacore import (Problem, Rule, ShiftType, SolveConfig, Worker, solve,
                         can_release, can_swap, cover_catastrophe)


def shifts():
    return [
        # Turno de 12 h (enfermería + auxiliares). "de 7 a 7": 07:00–19:00.
        ShiftType("MT", "Mañana-Tarde 07:00–19:00 (12 h)", hours=12, start="07:00", end="19:00"),
        # Turnos de la SUPERVISORA (mañanas, no 12 h).
        ShiftType("M7H", "Mañana 07:00–14:00", hours=7, start="07:00", end="14:00"),
        ShiftType("M", "Mañana (supervisora)", hours=7, start="08:00", end="15:00"),
        # No productivos (no cuentan como turno trabajado ni cubren dotación).
        ShiftType("D", "Descanso", hours=0, is_work=False, is_rest=True),
        ShiftType("VAC", "Vacaciones", hours=0, is_work=False),
        ShiftType("CJ", "Cómputo de jornada", hours=0, is_work=False),
        ShiftType("INT", "Baja laboral", hours=0, is_work=False),
    ]


# Enfermería rotatoria (DUE) con horas/mes objetivo. Noelia va aparte (supervisora).
DUE = [
    ("ARGUELLES GONZALEZ, ESTHER", 149.5), ("CAMPELO PANTIGA, DIEGO", 161.0),
    ("CANTO VALLINA, LUCIA", 161.0), ("CIENFUEGOS LLANEZA, SUSANA", 149.5),
    ("FERNANDEZ ESPINOSA, EVA", 126.5), ("FERNANDEZ FERNANDEZ, AIDA", 161.0),
    ("HERNANDO SANTAMARIA, ANGELA", 149.5), ("MARCOS FERNANDEZ, AINARA", 161.0),
    ("OLIVEIRA GARCIA, SUSANA", 149.5), ("PALACIO MAGDALENO, EVA MARIA", 161.0),
    ("RESTREPO FONSECA, ANA MARIA", 149.5), ("RODRIGUEZ MARTIN, SERGIO", 149.5),
    ("SANZ SUAREZ, HELGA", 161.0),
]

# Auxiliares (TCAE) — PLACEHOLDER hasta tener su hoja real (nombres y horas).
AUX = [(f"AUXILIAR {i+1}", 154.0) for i in range(8)]


def _sundays(year, month):
    d, out = dt.date(year, month, 1), []
    while d.month == month:
        if d.weekday() == 6:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def build_workers():
    ws = [Worker(id=f"due{i+1}", name=name, groups=["enfermera"],
                 skills=["enfermera"], allowed_shifts=["MT"], contract_hours=h)
          for i, (name, h) in enumerate(DUE)]
    ws += [Worker(id=f"aux{i+1}", name=name, groups=["auxiliar"],
                  skills=["auxiliar"], allowed_shifts=["MT"], contract_hours=h)
           for i, (name, h) in enumerate(AUX)]
    # Noelia Monteserín — SUPERVISORA (usuaria): turnos cortos, fuera del pool MT.
    ws.append(Worker(id="sup", name="MONTESERIN LOUGEDO, NOELIA",
                     groups=["supervisora"], skills=["supervisora"],
                     allowed_shifts=["M7H", "M"], contract_hours=154.0))
    return ws


def build_problem(workers, year=2026, month=10):
    start = dt.date(year, month, 1)
    H = ((dt.date(year, month + 1, 1) if month < 12 else dt.date(year + 1, 1, 1)) - start).days

    # SUPERVISORA: horario de mañanas FIJO (M7H lunes–viernes; descanso findes/festivos)
    for w in workers:
        if "supervisora" in w.groups:
            for d in range(H):
                w.fixed[d] = "M7H" if (start + dt.timedelta(days=d)).weekday() < 5 else "D"

    rota = {"groups": ["enfermera", "auxiliar"]}   # las reglas de 12 h NO aplican a la supervisora
    role_reqs = [{"shift": "MT", "skill": "enfermera", "min": 5},   # sin contar supervisora
                 {"shift": "MT", "skill": "auxiliar", "min": 3}]
    rules = [
        Rule("skill_coverage", mode="hard", tier=3, id="5 enf + 3 aux", params={
            "by_daytype": {"weekday": role_reqs, "weekend": role_reqs, "holiday": []}}),
        # domingos: cerrado (nadie hace MT)
        Rule("coverage", mode="hard", tier=3, id="domingos cerrado", params={
            "by_daytype": {"holiday": {"MT": {"min": 0, "max": 0}}}}),
        # turnos de 12 h: NO más de 2 días seguidos (solo rotación, no la supervisora)
        Rule("max_consecutive_work_days", mode="hard", tier=3, scope=rota,
             id="máx 2 días seguidos", params={"max": 2},
             citation={"ref": "Norma de la unidad (turnos de 12 h)"}),
        Rule("min_rest_hours_between_shifts", mode="hard", tier=3,
             id="descanso 12h", params={"min_hours": 12}),
        Rule("min_rest_days_in_window", mode="hard", tier=2, scope=rota,
             params={"days": 7, "min_rest": 2}),
        Rule("contract_hours", mode="soft", weight=6, tier=2),   # horas/mes por persona
        Rule("min_free_weekends", mode="soft", weight=4, tier=1, scope=rota, params={"min": 1}),
        Rule("balance", mode="soft", weight=2, tier=1, scope=rota, params={"dimension": "work"}),
    ]
    return Problem(H, shifts(), workers, rules=rules,
                   meta={"start_date": start.isoformat(), "holidays": _sundays(year, month)})


if __name__ == "__main__":
    prob = build_problem(build_workers(), 2026, 10)
    sol = solve(prob, SolveConfig(time_limit_s=25, deterministic=True))
    print("Planilla Oct-2026:", sol.status, "·", sol.stats["wall_time_s"], "s")
    sched = sol.schedule

    # comprobar cobertura por rol un día entre semana (índice 4 = lunes 5)
    enf = sum(1 for w in prob.workers if "enfermera" in w.skills and sched[w.id].get(4) == "MT")
    aux = sum(1 for w in prob.workers if "auxiliar" in w.skills and sched[w.id].get(4) == "MT")
    print(f"Día 5: {enf} enfermeras + {aux} auxiliares en MT (mín 5 + 3)")

    # ---- Las 3 preguntas de la supervisora ----
    print("\n¿Puede librar ARGUELLES (due1) el día 5?")
    print(" ", can_release(prob, sched, "due1", 4)["reason"])

    print("\n¿Cambio: due1 (día 5) ↔ due2 (día 5)?")
    print(" ", can_swap(prob, sched, "due1", 4, "due2", 4)["reason"])

    day = 4  # lunes 5
    caen = [w.id for w in prob.workers
            if "enfermera" in w.skills and sched[w.id].get(day) == "MT"][:3]
    nombres = ", ".join(next(w.name.split(",")[0] for w in prob.workers if w.id == i) for i in caen)
    print(f"\nCATÁSTROFE: faltan 3 enfermeras el día 5 ({nombres}) — ¿quién cubre?")
    cat = cover_catastrophe(prob, sched, {i: [day] for i in caen})
    print(f"  huecos: {cat['summary']['uncovered']}")
    for g in cat["gaps"]:
        top = ", ".join(c['name'].split(',')[0] for c in g["candidates"][:3])
        print(f"  día {g['day']+1} «{g['shift']}»/{g['skill']} faltan {g['need']-g['have']} → sustitutos: {top}")
