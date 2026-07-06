"""Tests de la lógica pura de sincronización (server/sync_api.py).

Sin dependencias: se ejecuta con  python3 tests/test_sync_api.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))

from sync_api import apply_changes, diff_month, resolve_sync_worker  # noqa: E402


def planilla():
    return {
        "year": 2026, "month": 10, "days": 31,
        "workers": [
            {"id": "w_ana", "name": "GARCIA LOPEZ, ANA", "role": "enfermera",
             "schedule": {"0": "MT", "1": "D", "2": "MT"}},
            {"id": "w_noe", "name": "MONTESERIN LOUGEDO, NOELIA",
             "role": "supervisora", "actaisId": "46",
             "schedule": {"0": "M7H", "1": "M7H"}},
        ],
    }


def test_resolve():
    p = planilla()
    assert resolve_sync_worker(p, actais_id="46")["id"] == "w_noe"
    assert resolve_sync_worker(p, name="GARCIA LOPEZ, ANA")["id"] == "w_ana"
    assert resolve_sync_worker(p, name="garcia lopez, ana")["id"] == "w_ana"
    # por apellidos, solo si es único
    assert resolve_sync_worker(p, name="MONTESERIN LOUGEDO, X")["id"] == "w_noe"
    assert resolve_sync_worker(p, actais_id="999", name="NADIE, X") is None


def test_diff_detecta_cambios():
    p = planilla()
    cells = ["MT", "VAC", "MT"] + [""] * 28          # día 2: D → VAC
    r = diff_month(p, p["workers"][0], 2026, 10, cells=cells)
    assert r["ok"] and not r["identical"]
    assert r["changes"] == [{"day": 1, "from": "D", "to": "VAC"}]
    # días 1 y 3 no cambian; días vacíos con turno en planilla → aviso, no borrado
    assert not any(c["day"] == 0 for c in r["changes"])


def test_diff_identico():
    p = planilla()
    r = diff_month(p, p["workers"][0], 2026, 10, cells=["MT", "D", "MT"] + [""] * 28)
    assert r["ok"] and r["identical"] and r["changes"] == []


def test_diff_mes_equivocado():
    p = planilla()
    r = diff_month(p, p["workers"][0], 2026, 11, cells=[""] * 31)
    assert not r["ok"] and "11/2026" in r["error"]


def test_diff_excluye_dias_ilegibles():
    p = planilla()
    cells = ["", "VAC", "MT"] + [""] * 28
    r = diff_month(p, p["workers"][0], 2026, 10, cells=cells,
                   unknown_days=[{"day": 1, "rawClass": "S_99"}])
    # el día ilegible NO entra en el diff, pero sí en los avisos
    assert r["ok"] and r["changes"] == []
    assert any("no se pudieron leer" in w for w in r["warnings"])


def test_diff_vacio_no_borra():
    p = planilla()
    # Actais vacío el día 1 (planilla dice MT): avisa, no propone borrar
    r = diff_month(p, p["workers"][0], 2026, 10, cells=[""] * 31)
    assert r["ok"] and r["changes"] == []
    assert any("vacíos" in w for w in r["warnings"])


def test_diff_modo_celda_suelta():
    p = planilla()
    r = diff_month(p, p["workers"][0], 2026, 10, changes=[{"day": 1, "to": "VAC"}])
    assert r["ok"] and r["changes"] == [{"day": 1, "from": "D", "to": "VAC"}]
    r2 = diff_month(p, p["workers"][0], 2026, 10, changes=[{"day": 0, "to": "MT"}])
    assert r2["ok"] and r2["identical"]


def test_diff_sin_worker():
    r = diff_month(planilla(), None, 2026, 10, cells=[""] * 31)
    assert not r["ok"] and "No encuentro" in r["error"]


def test_apply():
    p = planilla()
    p2, summary = apply_changes(p, "w_ana", [{"day": 1, "to": "VAC"}], actais_id="1122")
    w = p2["workers"][0]
    assert w["schedule"]["1"] == "VAC"
    assert w["actaisId"] == "1122"          # vínculo para futuros syncs
    assert "GARCIA" in summary and "d2" in summary


def test_apply_valida():
    for bad_args in (("w_nadie", [{"day": 1, "to": "VAC"}]),
                     ("w_ana", []),
                     ("w_ana", [{"day": 99, "to": "VAC"}])):
        try:
            apply_changes(planilla(), *bad_args)
            raise AssertionError(f"debió fallar: {bad_args}")
        except ValueError:
            pass


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} tests OK")
