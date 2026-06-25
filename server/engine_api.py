"""
Puente entre la planilla persistida (JSON) y el motor shiftia-core.

Forma normalizada de una planilla:
  {year, month, days, holidays:[domingos], workers:[{id,name,role,hours,schedule:{day:code}}]}
"""
import os
import re
import sys
import tempfile
import unicodedata

# importar los módulos del guante (carpeta padre) y el motor (shiftia-core)
_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_PARENT,
           os.path.join(_PARENT, "vendor"),                       # shiftia-core vendorizado (Docker)
           os.path.abspath(os.path.join(_PARENT, "..", "shiftia-core")),  # local (sibling)
           os.environ.get("SHIFTIA_CORE_PATH", "")):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

from pdf_reader import parse_planilla                       # noqa: E402
from dialisis import _sundays, shifts                       # noqa: E402
from analyze import dialisis_rules                          # noqa: E402
from shiftiacore import (Problem, Worker, audit_compliance,  # noqa: E402
                         can_release, can_swap, cover_catastrophe)

YEAR, MONTH, DAYS = 2026, 10, 31


def slug(name: str) -> str:
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    n = re.sub(r"[^a-zA-Z0-9]+", "_", n).strip("_").lower()
    return "w_" + n[:40]


def _role(name: str) -> str:
    return "supervisora" if "MONTESERIN" in name.upper() else "enfermera"


def _allowed(role: str):
    return ["M7H", "M"] if role == "supervisora" else ["MT"]


def planilla_from_pdf(raw: bytes) -> dict:
    fd, path = tempfile.mkstemp(suffix=".pdf")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        data = parse_planilla(path)
    finally:
        os.unlink(path)
    workers = []
    for name, w in data["workers"].items():
        role = _role(name)
        workers.append({"id": slug(name), "name": name, "role": role,
                        "hours": w["hours"],
                        "schedule": {str(d): c for d, c in w["schedule"].items()}})
    return {"year": YEAR, "month": MONTH, "days": DAYS,
            "holidays": [int(d.split("-")[2]) for d in _sundays(YEAR, MONTH)],
            "workers": workers}


def merge(existing: dict, new: dict) -> dict:
    """Funde por nombre: permite subir la hoja de enfermería y luego la de
    auxiliares y que la unidad quede completa."""
    if not existing:
        return new
    by = {w["name"].strip().upper(): w for w in existing.get("workers", [])}
    for w in new.get("workers", []):
        by[w["name"].strip().upper()] = w
    out = dict(existing)
    out["workers"] = list(by.values())
    for k in ("year", "month", "days", "holidays"):
        out[k] = new.get(k, existing.get(k))
    return out


def build_problem(p: dict):
    workers = [Worker(id=w["id"], name=w["name"], skills=[w["role"]], groups=[w["role"]],
                      allowed_shifts=_allowed(w["role"]), contract_hours=w.get("hours"))
               for w in p["workers"]]
    sched = {w["id"]: {int(d): c for d, c in w["schedule"].items()} for w in p["workers"]}
    prob = Problem(p["days"], shifts(), workers, rules=dialisis_rules(),
                   meta={"start_date": f'{p["year"]}-{p["month"]:02d}-01',
                         "holidays": _sundays(p["year"], p["month"])})
    return prob, sched


def audit_json(p: dict) -> dict:
    prob, sched = build_problem(p)
    rep = audit_compliance(prob, sched, rules=prob.rules)
    return {"compliant": rep.compliant,
            "checks": [{"id": c.rule_id, "status": c.status,
                        "citation": (c.citation or {}).get("ref"),
                        "issues": [i.detail for i in c.issues]} for c in rep.checks]}


def resolve(p: dict, ref):
    ids = {w["id"] for w in p["workers"]}
    if ref in ids:
        return ref
    refn = (ref or "").strip().upper()
    for w in p["workers"]:
        if w["name"].strip().upper() == refn:
            return w["id"]
    sur = refn.split(",")[0].strip()
    for w in p["workers"]:
        if w["name"].upper().split(",")[0].strip() == sur:
            return w["id"]
    return ref


def data_response(p: dict) -> dict:
    return {"ok": True, "year": p["year"], "month": p["month"], "days": p["days"],
            "holidays": p["holidays"], "updatedAt": p.get("_updated_at"),
            "workers": [{"id": w["id"], "name": w["name"], "role": w["role"],
                         "hours": w.get("hours"), "schedule": w["schedule"]}
                        for w in p["workers"]],
            "audit": audit_json(p)}


def answer(p: dict, action: str, cell: dict) -> dict:
    prob, sched = build_problem(p)
    if action in ("validateConvenio", "validar", "audit"):
        return audit_json(p)
    if action in ("cambio", "swap"):
        a = resolve(p, cell.get("worker_a") or cell.get("name_a"))
        b = resolve(p, cell.get("worker_b") or cell.get("name_b"))
        return can_swap(prob, sched, a, int(cell["day_a"]), b, int(cell["day_b"]))
    wid = resolve(p, cell.get("worker") or cell.get("worker_name") or cell.get("worker_id"))
    day = int(cell.get("day"))
    if action in ("librar", "release"):
        return can_release(prob, sched, wid, day)
    if action in ("whoCovers", "cover", "vacaciones"):
        return cover_catastrophe(prob, sched, {wid: [day]})
    return {"ok": False, "error": f"acción no soportada: {action}"}
