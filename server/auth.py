"""
Login con token (como la otra extensión).

La contraseña NO se guarda en claro: solo el hash sha256("USUARIO:clave").
/api/login valida y emite un JWT firmado; el resto de endpoints exigen ese token.
"""
import datetime as dt
import hashlib
import os

import jwt

# Credenciales por VARIABLES DE ENTORNO (Railway → Variables). Los valores
# de abajo son solo el arranque histórico: define SHIFTIA_USER y
# SHIFTIA_PASS_HASH en producción y rota la contraseña sin tocar código.
# Para generar el hash:  python -c "import hashlib;print(hashlib.sha256('USUARIO:clave'.encode()).hexdigest())"
USER = os.environ.get("SHIFTIA_USER", "NOEMONTS").strip().upper()
PASS_HASH = os.environ.get(
    "SHIFTIA_PASS_HASH",
    "071daa6e69ebc88173596087e7bcc97469279cdb380188ab05e7efa91d5231c7")

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-cambiar-en-railway")
if JWT_SECRET == "dev-secret-cambiar-en-railway":
    print("[shiftia] AVISO: JWT_SECRET sin definir — usando el secreto de "
          "desarrollo. Define JWT_SECRET en Railway.", flush=True)
JWT_ALG = "HS256"
TOKEN_DAYS = 30


def verify_credentials(user: str, password: str) -> bool:
    u = (user or "").strip().upper()
    h = hashlib.sha256(f"{u}:{password}".encode("utf-8")).hexdigest()
    return u == USER and h == PASS_HASH


def issue_token(user: str) -> str:
    now = dt.datetime.utcnow()
    return jwt.encode(
        {"sub": (user or "").upper(), "iat": now, "exp": now + dt.timedelta(days=TOKEN_DAYS)},
        JWT_SECRET, algorithm=JWT_ALG)


def verify_token(token: str):
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except Exception:
        return None
