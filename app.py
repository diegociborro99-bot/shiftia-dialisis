"""
Panel local de Shiftia · Diálisis.

Sube la planilla mensual (PDF de Actais) y contesta, sobre la planilla REAL,
las tres preguntas de la supervisora:
  · ¿puede librar un trabajador un día?
  · ¿es posible un cambio de turno?
  · catástrofe: ¿quién cubre?

Reusa el motor genérico shiftia-core (vía bootstrap). Sin dependencias nuevas
más allá de las del motor (ortools, pdfplumber).

Arranque:   python3 app.py        →  abre http://localhost:8770
"""
import bootstrap  # noqa: F401  (inserta ../shiftia-core en sys.path)

import base64
import json
import os
import tempfile
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from analyze import dialisis_rules, load
from dialisis import _sundays, shifts
from shiftiacore import (Problem, audit_compliance, can_release, can_swap,
                         cover_catastrophe)

YEAR, MONTH, DAYS = 2026, 10, 31          # la planilla de ejemplo es octubre 2026
PORT = 8770
HERE = os.path.dirname(os.path.abspath(__file__))

STATE = {"problem": None, "workers": [], "sched": {}}


# ---------------------------------------------------------------- motor ----
def _build(pdf_path):
    workers, sched = load(pdf_path, YEAR, MONTH)
    prob = Problem(DAYS, shifts(), workers, rules=dialisis_rules(),
                   meta={"start_date": f"{YEAR}-{MONTH:02d}-01",
                         "holidays": _sundays(YEAR, MONTH)})
    STATE.update(problem=prob, workers=workers, sched=sched)
    return prob, workers, sched


def _audit_json(prob, sched):
    rep = audit_compliance(prob, sched, rules=prob.rules)
    return {
        "compliant": rep.compliant,
        "checks": [{
            "id": c.rule_id,
            "status": c.status,
            "citation": (c.citation or {}).get("ref"),
            "issues": [i.detail for i in c.issues],
        } for c in rep.checks],
    }


def _load_response(prob, workers, sched):
    return {
        "ok": True, "year": YEAR, "month": MONTH, "days": DAYS,
        "holidays": [int(d.split("-")[2]) for d in _sundays(YEAR, MONTH)],
        "workers": [{
            "id": w.id, "name": w.name,
            "role": (w.skills or ["?"])[0],
            "hours": w.contract_hours,
            "schedule": {str(d): c for d, c in sched.get(w.id, {}).items()},
        } for w in workers],
        "audit": _audit_json(prob, sched),
    }


# --------------------------------------------------------------- rutas ----
def api_load(body):
    raw = base64.b64decode(body["pdf_base64"].split(",")[-1])
    fd, path = tempfile.mkstemp(suffix=".pdf")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        prob, workers, sched = _build(path)
    finally:
        os.unlink(path)
    return _load_response(prob, workers, sched)


def _resolve(ref):
    """id-o-nombre → id del trabajador (para que el Alt+clic de Actais case)."""
    if ref in STATE["sched"]:
        return ref
    refn = (ref or "").strip().upper()
    for w in STATE["workers"]:
        if (w.name or "").strip().upper() == refn:
            return w.id
    sur = refn.split(",")[0].strip()
    for w in STATE["workers"]:
        if (w.name or "").upper().split(",")[0].strip() == sur:
            return w.id
    return ref


def api_can_release(body):
    p, s = STATE["problem"], STATE["sched"]
    wid = _resolve(body.get("worker_id") or body.get("worker_name") or body.get("worker"))
    return can_release(p, s, wid, int(body["day"]))


def api_can_swap(body):
    p, s = STATE["problem"], STATE["sched"]
    a = _resolve(body.get("worker_a") or body.get("name_a"))
    b = _resolve(body.get("worker_b") or body.get("name_b"))
    return can_swap(p, s, a, int(body["day_a"]), b, int(body["day_b"]))


def api_cover(body):
    p, s = STATE["problem"], STATE["sched"]
    absences = {_resolve(k): [int(d) for d in v] for k, v in body["absences"].items()}
    return cover_catastrophe(p, s, absences, top=int(body.get("top", 5)))


def api_audit(body):
    return _audit_json(STATE["problem"], STATE["sched"])


ROUTES = {
    "/api/load": api_load,
    "/api/can-release": api_can_release,
    "/api/can-swap": api_can_swap,
    "/api/cover": api_cover,
    "/api/audit": api_audit,
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):           # silencio en consola
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):                 # preflight de la extensión
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send(self, obj, code=200):
        data = json.dumps(obj, default=str, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self._file("ui/index.html", "text/html; charset=utf-8")
        if path == "/logo.svg":
            return self._file("ui/logo.svg", "image/svg+xml")
        self.send_error(404)

    def _file(self, rel, ctype):
        fp = os.path.join(HERE, rel)
        if not os.path.exists(fp):
            return self.send_error(404)
        with open(fp, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        fn = ROUTES.get(self.path.split("?")[0])
        if not fn:
            return self.send_error(404)
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            if self.path != "/api/load" and STATE["problem"] is None:
                return self._send({"ok": False, "error": "Sube primero una planilla."}, 400)
            return self._send(fn(body))
        except Exception as e:  # noqa: BLE001
            return self._send({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"\n  Shiftia · Diálisis  →  {url}\n  (Ctrl+C para parar)\n")
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  parado.\n")


if __name__ == "__main__":
    main()
