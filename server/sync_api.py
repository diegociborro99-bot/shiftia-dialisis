"""
Sincronización Actais → planilla interna (la que vive en NUESTRA base de
datos; Actais nunca se toca).

Lógica pura (solo dicts), sin dependencias del motor: testeable en aislamiento.

Flujo en dos pasos, nunca sobrescritura ciega:
  1. diff_month()    → compara lo leído en pantalla con la planilla guardada
                       y devuelve las diferencias + avisos (celdas ilegibles,
                       días vacíos…). La usuaria las ve y confirma.
  2. apply_changes() → aplica SOLO los cambios confirmados y deja constancia
                       (el guardado versionado vive en db.py).
"""
import datetime as dt


def resolve_sync_worker(p: dict, actais_id=None, name=None):
    """Localiza al trabajador de la planilla que corresponde a lo que hay en
    pantalla. Prioridad: vínculo actaisId ya guardado → nombre completo →
    apellidos. Devuelve el dict del worker o None."""
    workers = p.get("workers", [])
    if actais_id is not None:
        aid = str(actais_id)
        for w in workers:
            if str(w.get("actaisId", "")) == aid:
                return w
    n = (name or "").strip().upper()
    if not n:
        return None
    for w in workers:
        if w["name"].strip().upper() == n:
            return w
    sur = n.split(",")[0].strip()
    if sur:
        hits = [w for w in workers
                if w["name"].upper().split(",")[0].strip() == sur]
        if len(hits) == 1:
            return hits[0]
    return None


def diff_month(p: dict, worker: dict, year: int, month: int,
               cells=None, changes=None, unknown_days=None):
    """Diferencias entre la planilla guardada y lo leído en Actais.

    - cells: array 0-based de 31 códigos ('' = sin código reconocido) — modo
      "mes completo" (botón Sincronizar del panel).
    - changes: [{day, to}] — modo "celda suelta" (menú Alt+clic).
    - unknown_days: [{day, ...}] días que la extensión NO supo leer; se
      excluyen del diff y se devuelven como aviso (nunca se propone borrar un
      día solo porque no supimos leerlo).

    Devuelve {ok, changes:[{day, from, to}], warnings:[str], worker:{id,name}}.
    """
    if not worker:
        return {"ok": False,
                "error": "No encuentro a ese trabajador en la planilla guardada. "
                         "Comprueba que su PDF está subido."}
    py, pm = int(p.get("year", 0)), int(p.get("month", 0))
    if int(year) != py or int(month) != pm:
        return {"ok": False,
                "error": f"La planilla guardada es de {pm:02d}/{py} y en Actais "
                         f"estás viendo {int(month):02d}/{int(year)}. Sube el PDF "
                         "de ese mes o cambia el mes en Actais."}

    days = int(p.get("days", 31))
    sched = worker.get("schedule", {})
    unknown = {int(u.get("day")) for u in (unknown_days or [])
               if isinstance(u, dict) and u.get("day") is not None}

    proposed = {}
    if changes is not None:
        for c in changes:
            d = int(c["day"])
            if not (0 <= d < days):
                return {"ok": False, "error": f"Día fuera de rango: {d + 1}"}
            proposed[d] = str(c.get("to") or "").strip().upper()
    elif cells is not None:
        for d in range(min(len(cells), days)):
            proposed[d] = str(cells[d] or "").strip().upper()
    else:
        return {"ok": False, "error": "Faltan datos: ni 'cells' ni 'changes'."}

    diffs, warnings = [], []
    empty_but_scheduled = []
    for d, new in sorted(proposed.items()):
        cur = str(sched.get(str(d), "") or "").strip().upper()
        if d in unknown:
            continue  # se avisa aparte, nunca se sincroniza un día ilegible
        if not new:
            # Celda vacía en Actais: si aquí hay turno, avisar, no borrar.
            if cur and changes is None:
                empty_but_scheduled.append(d + 1)
            elif changes is not None:
                diffs.append({"day": d, "from": cur, "to": ""})
            continue
        if new != cur:
            diffs.append({"day": d, "from": cur, "to": new})

    if unknown:
        warnings.append(
            f"{len(unknown)} día(s) no se pudieron leer en Actais "
            f"({', '.join(str(d + 1) for d in sorted(unknown))}) y se han excluido. "
            "Puede faltar mapear un código de turno nuevo.")
    if empty_but_scheduled:
        warnings.append(
            f"En Actais aparecen vacíos los días {', '.join(map(str, empty_but_scheduled))} "
            "que en la planilla tienen turno. No se borran automáticamente; "
            "revísalos a mano si Actais es lo correcto.")

    return {"ok": True,
            "worker": {"id": worker["id"], "name": worker["name"]},
            "changes": diffs, "warnings": warnings,
            "identical": not diffs}


def apply_changes(p: dict, worker_id: str, changes, actais_id=None):
    """Aplica los cambios confirmados sobre la planilla (en memoria) y
    devuelve (planilla, resumen legible). Lanza ValueError si algo no cuadra."""
    worker = next((w for w in p.get("workers", []) if w["id"] == worker_id), None)
    if not worker:
        raise ValueError(f"Trabajador no encontrado: {worker_id}")
    if not changes:
        raise ValueError("No hay cambios que aplicar.")
    days = int(p.get("days", 31))
    sched = worker.setdefault("schedule", {})
    parts = []
    for c in changes:
        d = int(c["day"])
        if not (0 <= d < days):
            raise ValueError(f"Día fuera de rango: {d + 1}")
        new = str(c.get("to") or "").strip().upper()
        old = str(sched.get(str(d), "") or "")
        if new:
            sched[str(d)] = new
        else:
            sched.pop(str(d), None)
        parts.append(f"d{d + 1}: {old or '∅'}→{new or '∅'}")
    if actais_id is not None:
        worker["actaisId"] = str(actais_id)  # vínculo para futuros syncs
    worker["_synced_at"] = dt.datetime.utcnow().isoformat()
    summary = f"Sync Actais · {worker['name']} · {len(parts)} cambio(s): " + "; ".join(parts)
    return p, summary
