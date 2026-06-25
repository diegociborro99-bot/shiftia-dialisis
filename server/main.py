"""
Backend de Shiftia · Diálisis (FastAPI).

Misma forma que la extensión que ya funciona:
  POST /api/login                 → {token}
  GET  /api/data                  → planilla + auditoría (de la BD)
  POST /api/import/pdf-upload      → sube PDF(s), parsea, fusiona y PERSISTE
  POST /api/assistant/{action}     → librar / whoCovers / validateConvenio / cambio

La planilla vive en la BASE DE DATOS (db.py). No se borra entre reinicios.

Local:   uvicorn main:app --port 8770     (usa SQLite ./planillas.db)
Railway: define DATABASE_URL (Postgres) y JWT_SECRET.
"""
import os
from contextlib import asynccontextmanager

from fastapi import Body, Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

import engine_api as E
from auth import issue_token, verify_credentials, verify_token
from db import init_db, load_planilla, save_planilla

UNIT = "dialisis"


@asynccontextmanager
async def lifespan(_app):
    init_db()                       # crea la tabla si no existe
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
    imported = 0
    for f in files:
        raw = await f.read()
        new = E.planilla_from_pdf(raw)
        p = E.merge(p, new)
        imported += len(new["workers"])
    save_planilla(UNIT, p)
    return {"ok": True, "workers": len(p["workers"]), "imported": imported}


@app.post("/api/assistant/{action}")
def assistant(action: str, body: dict = Body(default={}), _claims=Depends(auth)):
    p = load_planilla(UNIT)
    if not p:
        raise HTTPException(status_code=400, detail="No hay planilla. Sube el PDF primero.")
    cell = body.get("cell") if isinstance(body.get("cell"), dict) else body
    return E.answer(p, action, cell)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8770)))
