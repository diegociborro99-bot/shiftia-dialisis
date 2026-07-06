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

from sqlalchemy import DateTime, Integer, String, Text, create_engine, select
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


class PlanillaVersion(Base):
    """Historial: cada guardado (PDF, sync desde Actais, restauración) deja
    una versión. Permite auditar quién/qué/cuándo y deshacer un sync malo."""
    __tablename__ = "planilla_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    unit: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), default="pdf")   # pdf|sync|restore
    summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


MAX_VERSIONS = 200  # por unidad; las más antiguas se podan


def init_db():
    Base.metadata.create_all(engine)


def save_planilla(unit: str, data: dict, source: str = "pdf", summary: str = ""):
    data = {k: v for k, v in data.items() if not k.startswith("_updated")}
    payload = json.dumps(data, ensure_ascii=False, default=str)
    with Session(engine) as s:
        row = s.get(Planilla, unit)
        if row is None:
            s.add(Planilla(unit=unit, payload=payload))
        else:
            row.payload = payload
            row.updated_at = dt.datetime.utcnow()
        s.add(PlanillaVersion(unit=unit, payload=payload, source=source,
                              summary=summary[:2000]))
        # poda del historial
        ids = s.scalars(select(PlanillaVersion.id).where(PlanillaVersion.unit == unit)
                        .order_by(PlanillaVersion.id.desc()).offset(MAX_VERSIONS)).all()
        for vid in ids:
            v = s.get(PlanillaVersion, vid)
            if v:
                s.delete(v)
        s.commit()


def list_versions(unit: str, limit: int = 20):
    with Session(engine) as s:
        rows = s.scalars(select(PlanillaVersion).where(PlanillaVersion.unit == unit)
                         .order_by(PlanillaVersion.id.desc()).limit(limit)).all()
        return [{"id": v.id, "source": v.source, "summary": v.summary,
                 "created_at": v.created_at.isoformat()} for v in rows]


def load_version(unit: str, version_id: int):
    with Session(engine) as s:
        v = s.get(PlanillaVersion, int(version_id))
        if not v or v.unit != unit:
            return None
        return json.loads(v.payload)


def load_planilla(unit: str):
    with Session(engine) as s:
        row = s.get(Planilla, unit)
        if not row:
            return None
        data = json.loads(row.payload)
        data["_updated_at"] = row.updated_at.isoformat()
        return data
