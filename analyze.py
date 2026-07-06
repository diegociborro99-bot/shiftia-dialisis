"""
Cablea el LECTOR (pdf_reader) con el MOTOR (shiftia-core): lee una planilla PDF
real de Actais y, sobre ESOS datos, audita el convenio y contesta las preguntas
de la supervisora (librar / cambio / catástrofe).

Uso:  python analyze.py "RUTA.pdf"
"""
import bootstrap  # noqa: F401
import datetime as dt
import sys

from pdf_reader import parse_planilla
from dialisis import shifts, _sundays
from shiftiacore import (Problem, Rule, Worker, audit_compliance, can_release,
                         can_swap, cover_catastrophe)

PDF_DEFAULT = "planilla.pdf"   # ruta relativa; pásale la tuya como argumento


def load(path, year=2026, month=10):
    """PDF → (workers, schedule real). Esta hoja es DUE: enfermeras + supervisora."""
    data = parse_planilla(path)
    workers, sched = [], {}
    for i, (name, w) in enumerate(data["workers"].items()):
        wid = f"w{i}"
        role = "supervisora" if "MONTESERIN" in name.upper() else "enfermera"
        allowed = ["M7H", "M"] if role == "supervisora" else ["MT"]
        workers.append(Worker(id=wid, name=name, skills=[role], groups=[role],
                              allowed_shifts=allowed, contract_hours=w["hours"]))
        sched[wid] = dict(w["schedule"])
    return workers, sched


def dialisis_rules():
    # Turnos de 12 h: enfermería + auxiliares. La SUPERVISORA va aparte.
    rota = {"groups": ["enfermera", "auxiliar"]}
    # Dotación mínima por turno MT (sin contar a la supervisora):
    #   5 enfermeras + 3 auxiliares. (La usuaria indica 3-4 auxiliares; 3 es el
    #   suelo duro — ajustable a 4 en este punto si se confirma.)
    reqs = [{"shift": "MT", "skill": "enfermera", "min": 5},
            {"shift": "MT", "skill": "auxiliar", "min": 3}]
    return [
        Rule("skill_coverage", mode="hard", tier=3, id="5 enfermeras + 3 auxiliares",
             params={"by_daytype": {"weekday": reqs, "weekend": reqs, "holiday": []}},
             citation={"ref": "Dotación mínima de la unidad"}),
        Rule("coverage", mode="hard", tier=3, id="domingos cerrado",
             params={"by_daytype": {"holiday": {"MT": {"min": 0, "max": 0}}}}),
        Rule("max_consecutive_work_days", mode="hard", tier=3, scope=rota,
             id="máx 2 días seguidos", params={"max": 2},
             citation={"ref": "Norma de la unidad (turnos de 12 h)"}),
        Rule("min_rest_hours_between_shifts", mode="hard", tier=3,
             id="descanso 12 h", params={"min_hours": 12}),
    ]


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else PDF_DEFAULT
    workers, sched = load(path)
    prob = Problem(31, shifts(), workers, rules=dialisis_rules(),
                   meta={"start_date": "2026-10-01", "holidays": _sundays(2026, 10)})

    print(f"Planilla REAL leída del PDF: {len(workers)} trabajadores\n")
    rep = audit_compliance(prob, sched, rules=prob.rules)
    print("AUDITORÍA DE CONVENIO:", "✅ CONFORME" if rep.compliant else "❌ NO CONFORME")
    for c in rep.checks:
        mark = {"pass": "✓", "fail": "✗", "advisory": "·", "unchecked": "?"}[c.status]
        ref = f"  [{c.citation['ref']}]" if c.citation else ""
        print(f"  {mark} {c.rule_id}{ref}" + (f" — {len(c.issues)} incidencia(s)" if c.issues else ""))
        for i in c.issues[:3]:
            print(f"        · {i.detail}")

    enf = [w for w in workers if "enfermera" in w.skills]
    w0 = enf[0]
    day = next((d for d, c in sched[w0.id].items() if c == "MT"), 0)
    print(f"\n¿Puede librar {w0.name} el día {day+1}?")
    print("  ", can_release(prob, sched, w0.id, day)["reason"])

    w1 = enf[1]
    print(f"\n¿Cambio {w0.name.split(',')[0]} (día {day+1}) ↔ {w1.name.split(',')[0]} (día {day+1})?")
    print("  ", can_swap(prob, sched, w0.id, day, w1.id, day)["reason"])

    caen = [w.id for w in enf if sched[w.id].get(day) == "MT"][:4]
    print(f"\nCATÁSTROFE: faltan {len(caen)} enfermeras el día {day+1} — ¿quién cubre?")
    cat = cover_catastrophe(prob, sched, {i: [day] for i in caen})
    print(f"  huecos: {cat['summary']['uncovered']}")
    for g in cat["gaps"]:
        falta = g["need"] - g["have"]
        if g["candidates"]:
            top = ", ".join(c["name"].split(",")[0] for c in g["candidates"][:3])
            print(f"  día {g['day']+1} «{g['shift']}»/{g['skill']} faltan {falta} → {top}")
        else:
            print(f"  día {g['day']+1} «{g['shift']}»/{g['skill']} faltan {falta} → "
                  f"sin sustituto directo legal (habría que reorganizar la planilla)")
