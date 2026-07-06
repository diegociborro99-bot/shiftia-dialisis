"""
Persistencia de las planillas. La VERDAD vive aquí (base de datos), no en
memoria: sobrevive a reinicios y redeploys.

Modelo MULTI-PLANILLA: se guardan varias planillas, cada una con su NOMBRE,
año/mes y su payload JSON (empleados + parámetros + horario). Clave = id.

- En la nube (Railway): Postgres vía DATABASE_URL.
- En local / tests: SQLite en un fichero (también persistente).

El mismo código sirve para ambos (SQLAlchemy). Para cambiar a Supabase basta
con poner su DATABASE_URL.
"""
import datetime as dt
import json
import os

from sqlalchemy import (DateTime, Integer, String, Text, create_engine, inspect,
                        text)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./planillas.db")
# Railway/Heroku entregan a veces 'postgres://'; SQLAlchemy quiere 'postgresql://'
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, echo=False, pool_pre_ping=True, connect_args=_args)


def _now():
    return dt.datetime.utcnow()


class Base(DeclarativeBase):
    pass


class Planilla(Base):
    __tablename__ = "planillas"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    year: Mapped[int] = mapped_column(Integer, default=0)
    month: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[str] = mapped_column(Text)                 # JSON de la planilla
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


# ------------------------------------------------------------------ init ----
def init_db():
    """Crea la tabla si no existe. Si detecta el esquema VIEJO (una sola
    planilla, clave 'unit'), migra esa fila al modelo nuevo sin perder datos."""
    insp = inspect(engine)
    if insp.has_table("planillas"):
        cols = {c["name"] for c in insp.get_columns("planillas")}
        if "id" not in cols:            # esquema viejo → migrar
            _migrate_legacy()
            return
    Base.metadata.create_all(engine)


def _migrate_legacy():
    old = []
    with engine.connect() as c:
        for row in c.execute(text("SELECT unit, payload FROM planillas")):
            old.append((row[0], row[1]))
    with engine.begin() as c:
        c.execute(text("DROP TABLE planillas"))
    Base.metadata.create_all(engine)
    for unit, payload in old:
        try:
            data = json.loads(payload)
        except Exception:
            continue
        data["id"] = unit
        data.setdefault("name", "Diálisis")
        save_planilla(data)


# --------------------------------------------------------------- CRUD -------
def save_planilla(data: dict):
    """Inserta o actualiza una planilla. `data` debe traer 'id'."""
    pid = data["id"]
    payload = json.dumps(data, ensure_ascii=False, default=str)
    with Session(engine) as s:
        row = s.get(Planilla, pid)
        if row is None:
            s.add(Planilla(id=pid, name=data.get("name", ""),
                           year=int(data.get("year") or 0),
                           month=int(data.get("month") or 0),
                           payload=payload))
        else:
            row.name = data.get("name", row.name)
            row.year = int(data.get("year") or 0)
            row.month = int(data.get("month") or 0)
            row.payload = payload
            row.updated_at = _now()
        s.commit()


def load_planilla(pid: str):
    with Session(engine) as s:
        row = s.get(Planilla, pid)
        if not row:
            return None
        data = json.loads(row.payload)
        data["id"] = row.id
        data["name"] = row.name
        data["_updated_at"] = row.updated_at.isoformat()
        data["_created_at"] = row.created_at.isoformat()
        return data


def list_planillas():
    """Metadatos de todas las planillas (sin parsear el payload aquí)."""
    with Session(engine) as s:
        rows = s.query(Planilla).order_by(Planilla.updated_at.desc()).all()
        return [{"id": r.id, "name": r.name, "year": r.year, "month": r.month,
                 "updated_at": r.updated_at.isoformat(), "payload": r.payload}
                for r in rows]


def delete_planilla(pid: str) -> bool:
    with Session(engine) as s:
        row = s.get(Planilla, pid)
        if not row:
            return False
        s.delete(row)
        s.commit()
        return True
