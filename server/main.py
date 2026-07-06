"""
Backend de Shiftia · Diálisis (FastAPI).

Guarda VARIAS planillas (cada una con nombre, año/mes, empleados con sus
parámetros y su horario) en la base de datos (db.py). No se borran entre
reinicios.

Web (dashboard):    GET  /                              → panel de la supervisora
Auth:               POST /api/login                     → {token}

Planillas (multi):
  GET    /api/planillas                                 → lista (nombre, mes, nº empleados, conforme)
  POST   /api/planillas                                 → crear {name, year, month}
  GET    /api/planillas/{id}                            → planilla completa + auditoría
  PATCH  /api/planillas/{id}                            → renombrar / cambiar mes-año
  DELETE /api/planillas/{id}                            → borrar
  POST   /api/planillas/{id}/import                     → subir PDF(s) y fusionar
  POST   /api/planillas/{id}/worker                     → añadir empleado
  PATCH  /api/planillas/{id}/worker/{wid}               → editar parámetros (rol, horas, nombre)
  DELETE /api/planillas/{id}/worker/{wid}               → quitar empleado
  PUT    /api/planillas/{id}/cell                        → fijar una celda de horario (sync manual)
  POST   /api/planillas/{id}/assistant/{action}         → librar / whoCovers / validar / cambio

Legacy (la extensión de Chrome existente, sobre la planilla por defecto):
  GET  /api/data · POST /api/import/pdf-upload · POST /api/assistant/{action}

Local:   uvicorn main:app --port 8770     (usa SQLite ./planillas.db)
Railway: define DATABASE_URL (Postgres) y JWT_SECRET.
"""
import os
from contextlib import asynccontextmanager

from fastapi import Body, Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

import engine_api as E
from auth import issue_token, verify_credentials, verify_token
from db import (delete_planilla, init_db, list_planillas, load_planilla,
                save_planilla)

DEFAULT_ID = "dialisis"          # planilla que usa la extensión legacy
HERE = os.path.dirname(os.path.abspath(__file__))
WEB_INDEX = os.path.join(HERE, "web", "index.html")


@asynccontextmanager
async def lifespan(_app):
    init_db()                       # crea/migra la tabla
    yield


app = FastAPI(title="Shiftia · Diálisis API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])


def auth(authorization: str = Header(default="")):
    token = (authorization or "").replace("Bearer ", "").strip()
    claims = verify_token(token)
    if not claims:
        raise HTTPException(status_code=401, detail="Sesión caducada o ausente")
    return claims


def _get(pid: str) -> dict:
    p = load_planilla(pid)
    if not p:
        raise HTTPException(status_code=404, detail="Planilla no encontrada")
    return p


# ------------------------------------------------------------- web / auth ---
@app.get("/", response_class=HTMLResponse)
def home():
    try:
        with open(WEB_INDEX, encoding="utf-8") as f:
            return HTMLResponse(f.read())
    except FileNotFoundError:
        return HTMLResponse("<h1>Shiftia · Diálisis</h1><p>API activa. "
                            "Falta el front-end (server/web/index.html).</p>")


@app.get("/api/health")
def health():
    return {"ok": True, "service": "shiftia-dialisis"}


@app.post("/api/login")
def login(body: dict = Body(...)):
    if not verify_credentials(body.get("user"), body.get("password")):
        raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos")
    return {"ok": True, "token": issue_token(body.get("user"))}


# ---------------------------------------------------------- planillas -------
@app.get("/api/planillas")
def planillas(_claims=Depends(auth)):
    return {"ok": True, "planillas": [E.summary(r) for r in list_planillas()]}


@app.post("/api/planillas")
def create_planilla(body: dict = Body(...), _claims=Depends(auth)):
    name = (body.get("name") or "").strip() or "Planilla sin nombre"
    year = int(body.get("year") or 2026)
    month = int(body.get("month") or 1)
    if not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="Mes fuera de rango (1-12)")
    existing = {r["id"] for r in list_planillas()}
    pid = E.unique_id(E.new_id(name), existing)
    p = E.empty_planilla(pid, name, year, month)
    save_planilla(p)
    return {"ok": True, "id": pid}


@app.get("/api/planillas/{pid}")
def get_planilla(pid: str, _claims=Depends(auth)):
    return E.data_response(_get(pid))


@app.patch("/api/planillas/{pid}")
def patch_planilla(pid: str, body: dict = Body(...), _claims=Depends(auth)):
    p = _get(pid)
    if body.get("name") is not None:
        p["name"] = (body["name"] or "").strip() or p["name"]
    if body.get("year") is not None or body.get("month") is not None:
        p["year"] = int(body.get("year") or p["year"])
        p["month"] = int(body.get("month") or p["month"])
        if not (1 <= p["month"] <= 12):
            raise HTTPException(status_code=400, detail="Mes fuera de rango (1-12)")
        p["days"] = E.days_in_month(p["year"], p["month"])
        p["holidays"] = E.holidays_for(p["year"], p["month"])
    save_planilla(p)
    return {"ok": True}


@app.delete("/api/planillas/{pid}")
def del_planilla(pid: str, _claims=Depends(auth)):
    return {"ok": delete_planilla(pid)}


@app.post("/api/planillas/{pid}/import")
async def import_pdf(pid: str, files: list[UploadFile] = File(...), _claims=Depends(auth)):
    p = _get(pid)
    imported = 0
    for f in files:
        raw = await f.read()
        new = E.planilla_from_pdf(raw, p["year"], p["month"])
        p = E.merge(p, new)
        imported += len(new["workers"])
    save_planilla(p)
    return {"ok": True, "workers": len(p["workers"]), "imported": imported}


# ---- empleados (parámetros + horario) ----
@app.post("/api/planillas/{pid}/worker")
def add_worker(pid: str, body: dict = Body(...), _claims=Depends(auth)):
    p = _get(pid)
    name = (body.get("name") or "").strip() or "SIN NOMBRE"
    w = E.add_worker(p, name, body.get("role") or "enfermera", body.get("hours"))
    save_planilla(p)
    return {"ok": True, "id": w["id"]}


@app.patch("/api/planillas/{pid}/worker/{wid}")
def edit_worker(pid: str, wid: str, body: dict = Body(...), _claims=Depends(auth)):
    p = _get(pid)
    hours = body["hours"] if "hours" in body else "__keep__"
    if not E.update_worker(p, wid, name=body.get("name"), role=body.get("role"), hours=hours):
        raise HTTPException(status_code=404, detail="Empleado no encontrado")
    save_planilla(p)
    return {"ok": True}


@app.delete("/api/planillas/{pid}/worker/{wid}")
def remove_worker(pid: str, wid: str, _claims=Depends(auth)):
    p = _get(pid)
    if not E.delete_worker(p, wid):
        raise HTTPException(status_code=404, detail="Empleado no encontrado")
    save_planilla(p)
    return {"ok": True}


@app.put("/api/planillas/{pid}/cell")
def put_cell(pid: str, body: dict = Body(...), _claims=Depends(auth)):
    p = _get(pid)
    wid = E.resolve(p, body.get("worker") or body.get("worker_id") or body.get("worker_name"))
    if not E.set_cell(p, wid, int(body["day"]), body.get("code") or "D"):
        raise HTTPException(status_code=404, detail="Empleado no encontrado")
    save_planilla(p)
    return {"ok": True}


@app.post("/api/planillas/{pid}/assistant/{action}")
def planilla_assistant(pid: str, action: str, body: dict = Body(default={}),
                       _claims=Depends(auth)):
    p = _get(pid)
    cell = body.get("cell") if isinstance(body.get("cell"), dict) else body
    return E.answer(p, action, cell)


# ---- sincronización con Actais (extensión ↔ planilla guardada) ----
@app.get("/api/match")
def match_planilla(year: int, month: int, _claims=Depends(auth)):
    """Planillas cuyo mes/año coincide con el que la extensión ve en Actais.
    La extensión usa esto para saber a qué planilla volcar el cambio."""
    hits = [E.summary(r) for r in list_planillas()
            if int(r.get("year") or 0) == year and int(r.get("month") or 0) == month]
    return {"ok": True, "planillas": hits}


@app.post("/api/planillas/{pid}/sync")
def sync_planilla(pid: str, body: dict = Body(...), _claims=Depends(auth)):
    """Vuelca a la planilla guardada el mes de un empleado tal como está en
    Actais. Devuelve el diff de lo que cambió (para que web y extensión bailen
    a la vez) y la auditoría recalculada."""
    p = _get(pid)
    # Coteja el mes/año de la pantalla de Actais con el de la planilla guardada:
    # así el volcado cae en el MES correcto (no en otro mes por error).
    y, m = body.get("year"), body.get("month")
    if y is not None and m is not None and (int(y) != p["year"] or int(m) != p["month"]):
        raise HTTPException(
            status_code=409,
            detail=(f"La pantalla es {int(m):02d}/{int(y)} pero esta planilla es "
                    f"{p['month']:02d}/{p['year']}. Elige la planilla de ese mes."))
    cells = body.get("cells") or body.get("schedule") or {}
    res = E.sync_worker_month(
        p, body.get("worker") or body.get("worker_name"), cells,
        worker_id=body.get("worker_id"), role=body.get("role"), hours=body.get("hours"))
    save_planilla(p)
    res["ok"] = True
    res["year"] = p["year"]
    res["month"] = p["month"]
    res["audit"] = E.audit_json(p)
    return res


# ------------------------------------------------------- legacy (extensión) -
def _default():
    return load_planilla(DEFAULT_ID)


@app.get("/api/data")
def get_data(_claims=Depends(auth)):
    p = _default()
    if not p:
        return {"ok": True, "workers": [], "empty": True}
    return E.data_response(p)


@app.post("/api/import/pdf-upload")
async def legacy_upload(files: list[UploadFile] = File(...), _claims=Depends(auth)):
    p = _default() or E.empty_planilla(DEFAULT_ID, "Diálisis", 2026, 10)
    imported = 0
    for f in files:
        raw = await f.read()
        new = E.planilla_from_pdf(raw, p["year"], p["month"])
        p = E.merge(p, new)
        imported += len(new["workers"])
    save_planilla(p)
    return {"ok": True, "workers": len(p["workers"]), "imported": imported}


@app.post("/api/assistant/{action}")
def legacy_assistant(action: str, body: dict = Body(default={}), _claims=Depends(auth)):
    p = _default()
    if not p:
        raise HTTPException(status_code=400, detail="No hay planilla. Sube el PDF primero.")
    cell = body.get("cell") if isinstance(body.get("cell"), dict) else body
    return E.answer(p, action, cell)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8770)))
