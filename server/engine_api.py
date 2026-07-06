"""
Puente entre las planillas persistidas (JSON) y el motor shiftia-core.

Forma normalizada de una planilla:
  {id, name, year, month, days, holidays:[domingos],
   workers:[{id, name, role, hours, schedule:{day:code}}]}

El año/mes/festivos/días de cada planilla se derivan de SU año/mes (no hay nada
hardcodeado): cada planilla lleva su propio calendario.
"""
import datetime as dt
import json
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

ROLES = ("enfermera", "auxiliar", "supervisora")


# ------------------------------------------------------------- calendario ---
def days_in_month(year: int, month: int) -> int:
    start = dt.date(year, month, 1)
    end = dt.date(year + 1, 1, 1) if month == 12 else dt.date(year, month + 1, 1)
    return (end - start).days


def holidays_for(year: int, month: int):
    """Domingos del mes como días 1..N (la unidad cierra en domingo)."""
    return [int(d.split("-")[2]) for d in _sundays(year, month)]


# ---------------------------------------------------------------- ids -------
def _slugify(s: str) -> str:
    n = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    n = re.sub(r"[^a-zA-Z0-9]+", "_", n).strip("_").lower()
    return n[:40]


def slug(name: str) -> str:
    return "w_" + (_slugify(name) or "sn")


def new_id(name: str) -> str:
    return "p_" + (_slugify(name) or "planilla")


def unique_id(candidate: str, existing) -> str:
    existing = set(existing)
    if candidate not in existing:
        return candidate
    k = 2
    while f"{candidate}_{k}" in existing:
        k += 1
    return f"{candidate}_{k}"


# ------------------------------------------------------------- empleados ----
def _role(name: str) -> str:
    return "supervisora" if "MONTESERIN" in (name or "").upper() else "enfermera"


def _allowed(role: str):
    return ["M7H", "M"] if role == "supervisora" else ["MT"]


# ----------------------------------------------------------- planillas ------
def empty_planilla(pid: str, name: str, year: int, month: int) -> dict:
    return {"id": pid, "name": name, "year": int(year), "month": int(month),
            "days": days_in_month(year, month),
            "holidays": holidays_for(year, month), "workers": []}


def planilla_from_pdf(raw: bytes, year: int, month: int) -> dict:
    """Lee un PDF de Actais y lo normaliza al calendario del año/mes dados."""
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
    return {"year": int(year), "month": int(month),
            "days": days_in_month(year, month),
            "holidays": holidays_for(year, month), "workers": workers}


def merge(existing: dict, new: dict) -> dict:
    """Funde por nombre: permite subir la hoja de enfermería y luego la de
    auxiliares y que la unidad quede completa. Conserva id/name/calendario de
    la planilla existente."""
    if not existing:
        return new
    by = {w["name"].strip().upper(): w for w in existing.get("workers", [])}
    for w in new.get("workers", []):
        by[w["name"].strip().upper()] = w
    out = dict(existing)
    out["workers"] = list(by.values())
    return out


def add_worker(p: dict, name: str, role: str = "enfermera", hours=None) -> dict:
    role = role if role in ROLES else "enfermera"
    existing = {w["id"] for w in p["workers"]}
    wid = unique_id(slug(name), existing)
    w = {"id": wid, "name": name, "role": role,
         "hours": (float(hours) if hours not in (None, "") else None),
         "schedule": {str(d): "D" for d in range(p["days"])}}
    p["workers"].append(w)
    return w


def update_worker(p: dict, wid: str, name=None, role=None, hours="__keep__") -> dict:
    for w in p["workers"]:
        if w["id"] == wid:
            if name is not None:
                w["name"] = name
            if role is not None and role in ROLES:
                w["role"] = role
            if hours != "__keep__":
                w["hours"] = (float(hours) if hours not in (None, "") else None)
            return w
    return None


def delete_worker(p: dict, wid: str) -> bool:
    before = len(p["workers"])
    p["workers"] = [w for w in p["workers"] if w["id"] != wid]
    return len(p["workers"]) < before


def set_cell(p: dict, wid: str, day: int, code: str) -> dict:
    for w in p["workers"]:
        if w["id"] == wid:
            w.setdefault("schedule", {})[str(int(day))] = (code or "D").strip()
            return w
    return None


def sync_worker_month(p: dict, worker_name, cells: dict, worker_id=None,
                      role=None, hours=None) -> dict:
    """Concilia el mes de UN empleado leído de Actais contra la planilla guardada.

    Actais es la fuente de la verdad del turno real: si en pantalla cambió algo,
    aquí se aplica. Devuelve el diff ({day, from, to}) para que la extensión y la
    web muestren exactamente qué se movió. Si el empleado no existía, se crea.
    """
    w = None
    if worker_id:
        rid = resolve(p, worker_id)
        w = next((x for x in p["workers"] if x["id"] == rid), None)
    if w is None and worker_name:
        rid = resolve(p, worker_name)
        w = next((x for x in p["workers"] if x["id"] == rid), None)
    created = False
    if w is None:
        w = add_worker(p, worker_name or "SIN NOMBRE", role or "enfermera", hours)
        created = True

    changes = []
    sched = w.setdefault("schedule", {})
    for d, code in (cells or {}).items():
        try:
            d = str(int(d))
        except (TypeError, ValueError):
            continue
        code = (code or "D").strip()
        old = sched.get(d)
        if old != code:
            changes.append({"day": int(d), "from": old, "to": code})
            sched[d] = code

    if role and role in ROLES:
        w["role"] = role
    if hours not in (None, ""):
        try:
            w["hours"] = float(hours)
        except (TypeError, ValueError):
            pass

    return {"worker": w["id"], "name": w["name"], "role": w["role"],
            "created": created, "changes": changes, "schedule": w["schedule"]}


# ------------------------------------------------------------- motor --------
def build_problem(p: dict):
    workers = [Worker(id=w["id"], name=w["name"], skills=[w["role"]], groups=[w["role"]],
                      allowed_shifts=_allowed(w["role"]), contract_hours=w.get("hours"))
               for w in p["workers"]]
    sched = {w["id"]: {int(d): c for d, c in w.get("schedule", {}).items()}
             for w in p["workers"]}
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


# ------------------------------------------------------------- salida -------
def summary(row: dict) -> dict:
    """Metadatos + recuento + conformidad de una planilla, para la lista."""
    try:
        p = json.loads(row["payload"])
    except Exception:
        p = {}
    out = {"id": row["id"], "name": row.get("name") or p.get("name", ""),
           "year": row.get("year") or p.get("year"),
           "month": row.get("month") or p.get("month"),
           "days": p.get("days"), "workers": len(p.get("workers", [])),
           "updatedAt": row.get("updated_at")}
    try:
        out["compliant"] = audit_json({**p, "id": row["id"]})["compliant"] \
            if p.get("workers") else None
    except Exception:
        out["compliant"] = None
    return out


def data_response(p: dict) -> dict:
    return {"ok": True, "id": p.get("id"), "name": p.get("name"),
            "year": p["year"], "month": p["month"], "days": p["days"],
            "holidays": p["holidays"], "updatedAt": p.get("_updated_at"),
            "workers": [{"id": w["id"], "name": w["name"], "role": w["role"],
                         "hours": w.get("hours"), "schedule": w.get("schedule", {})}
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
