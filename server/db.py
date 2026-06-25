"""
Persistencia de las planillas. La VERDAD vive aquí (base de datos), no en
memoria: sobrevive a reinicios y redeploys.

- En la nube (Railway): Postgres vía DATABASE_URL.
- En local / tests: SQLite en un fichero (también persistente).

El mismo código sirve para ambos (SQLAlchemy). Para cambiar a Supabase basta
con poner su DATABASE_URL.
"""
import datetime as dt
import json
import os

from sqlalchemy import DateTime, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./planillas.db")
# Railway/Heroku entregan a veces 'postgres://'; SQLAlchemy quiere 'postgresql://'
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, echo=False, pool_pre_ping=True, connect_args=_args)


class Base(DeclarativeBase):
    pass


class Planilla(Base):
    __tablename__ = "planillas"
    unit: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload: Mapped[str] = mapped_column(Text)                 # JSON de la planilla
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


def init_db():
    Base.metadata.create_all(engine)


def save_planilla(unit: str, data: dict):
    with Session(engine) as s:
        row = s.get(Planilla, unit)
        if row is None:
            s.add(Planilla(unit=unit, payload=json.dumps(data, ensure_ascii=False, default=str)))
        else:
            row.payload = json.dumps(data, ensure_ascii=False, default=str)
            row.updated_at = dt.datetime.utcnow()
        s.commit()


def load_planilla(unit: str):
    with Session(engine) as s:
        row = s.get(Planilla, unit)
        if not row:
            return None
        data = json.loads(row.payload)
        data["_updated_at"] = row.updated_at.isoformat()
        return data
