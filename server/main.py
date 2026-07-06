"""
Backend de Shiftia · Diálisis (FastAPI).

  POST /api/login                 → {token}
  GET  /api/data                  → planilla + auditoría (de la BD)
  POST /api/import/pdf-upload     → sube PDF(s), parsea, fusiona y PERSISTE
  POST /api/assistant/{action}    → librar / whoCovers / validateConvenio / cambio
  POST /api/sync/preview          → diff planilla guardada vs lo leído en Actais
  POST /api/sync/apply            → aplica los cambios confirmados (versionado)
  GET  /api/history               → últimas versiones de la planilla
  POST /api/history/restore       → restaura una versión anterior

La planilla vive en la BASE DE DATOS (db.py) con historial de versiones.

Local:   uvicorn main:app --port 8770     (usa SQLite ./planillas.db)
Railway: define DATABASE_URL (Postgres), JWT_SECRET y (opcional)
         SHIFTIA_USER / SHIFTIA_PASS_HASH / ALLOWED_ORIGINS.
"""
import os
from contextlib import asynccontextmanager

from fastapi import Body, Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

import engine_api as E
import sync_api as S
from auth import issue_token, verify_credentials, verify_token
from db import init_db, list_versions, load_planilla, load_version, save_planilla

UNIT = "dialisis"


@asynccontextmanager
async def lifespan(_app):
    init_db()                       # crea las tablas si no existen
    yield


app = FastAPI(title="Shiftia · Diálisis API", lifespan=lifespan)
# ALLOWED_ORIGINS: lista separada por comas, p. ej.
#   chrome-extension://<id-de-la-extension>
# Sin definir se mantiene abierto (compatibilidad con la instalación actual).
_origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_origins,
                   allow_methods=["*"], allow_headers=["*"])


def auth(authorization: str = Header(default="")):
    token = (authorization or "").replace("Bearer ", "").strip()
    claims = verify_token(token)
    if not claims:
        raise HTTPException(status_code=401, detail="Sesión caducada o ausente")
    return claims


@app.get("/")
def health():
    return {"ok": True, "service": "shiftia-dialisis"}


@app.post("/api/login")
def login(body: dict = Body(...)):
    if not verify_credentials(body.get("user"), body.get("password")):
        raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos")
    return {"ok": True, "token": issue_token(body.get("user"))}


@app.get("/api/data")
def get_data(_claims=Depends(auth)):
    p = load_planilla(UNIT)
    if not p:
        return {"ok": True, "workers": [], "empty": True}
    return E.data_response(p)


@app.post("/api/import/pdf-upload")
async def upload(files: list[UploadFile] = File(...), _claims=Depends(auth)):
    p = load_planilla(UNIT)
    imported, names = 0, []
    for f in files:
        raw = await f.read()
        new = E.planilla_from_pdf(raw)
        p = E.merge(p, new)
        imported += len(new["workers"])
        names.append(f.filename or "pdf")
    save_planilla(UNIT, p, source="pdf",
                  summary=f"PDF: {', '.join(names)} · {imported} trabajador(es)")
    return {"ok": True, "workers": len(p["workers"]), "imported": imported}


@app.post("/api/assistant/{action}")
def assistant(action: str, body: dict = Body(default={}), _claims=Depends(auth)):
    p = load_planilla(UNIT)
    if not p:
        raise HTTPException(status_code=400, detail="No hay planilla. Sube el PDF primero.")
    cell = body.get("cell") if isinstance(body.get("cell"), dict) else body
    return E.answer(p, action, cell)


def _planilla_or_400():
    p = load_planilla(UNIT)
    if not p:
        raise HTTPException(status_code=400, detail="No hay planilla. Sube el PDF primero.")
    return p


@app.post("/api/sync/preview")
def sync_preview(body: dict = Body(...), _claims=Depends(auth)):
    """Compara lo leído en la pantalla de Actais con la planilla guardada.
    NUNCA escribe: devuelve el diff para que la usuaria lo confirme."""
    p = _planilla_or_400()
    worker = S.resolve_sync_worker(p, actais_id=body.get("actaisId"),
                                   name=body.get("workerName"))
    try:
        year, month = int(body.get("year", 0)), int(body.get("month", 0))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="year/month inválidos")
    return S.diff_month(p, worker, year, month,
                        cells=body.get("cells"), changes=body.get("changes"),
                        unknown_days=body.get("unknownDays"))


@app.post("/api/sync/apply")
def sync_apply(body: dict = Body(...), _claims=Depends(auth)):
    """Aplica los cambios que la usuaria confirmó en el preview. Guarda una
    versión nueva (historial) — Actais no se toca, solo NUESTRA planilla."""
    p = _planilla_or_400()
    try:
        p, summary = S.apply_changes(p, body.get("workerId"),
                                     body.get("changes"),
                                     actais_id=body.get("actaisId"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    save_planilla(UNIT, p, source="sync", summary=summary)
    return {"ok": True, "applied": len(body.get("changes") or []),
            "summary": summary, "audit": E.audit_json(p)}


@app.get("/api/history")
def history(_claims=Depends(auth)):
    return {"ok": True, "versions": list_versions(UNIT)}


@app.post("/api/history/restore")
def restore(body: dict = Body(...), _claims=Depends(auth)):
    data = load_version(UNIT, body.get("versionId") or 0)
    if data is None:
        raise HTTPException(status_code=404, detail="Versión no encontrada")
    save_planilla(UNIT, data, source="restore",
                  summary=f"Restaurada la versión #{body.get('versionId')}")
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8770)))
