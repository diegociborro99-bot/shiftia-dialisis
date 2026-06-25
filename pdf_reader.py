"""
Lector de planillas PDF de Actais (Fundación Hospital de Jove).

Parsea el PDF mensual exportado de Actais → estructura interna
{trabajador: {día(0-based): código}} + horas, y la LEYENDA de códigos del propio
PDF (para auto-configurar el mapa de turnos sin hardcodear nada).

Genérico: sirve para cualquier unidad cuyo PDF tenga este formato (bloque por
trabajador: fila de nº de día + fila de códigos, nombre a la izquierda).

Uso:  python pdf_reader.py "RUTA.pdf"
"""
import re
import sys

import pdfplumber


def _to_hours(s):
    s = (s or "").strip().replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


_LEG = re.compile(r"^(D|MT|M7H|M|G\d{2})\s+(.+?)(?:\s+(?:G\d{2}|D|MT|M7H|M)\s.*)?$")


def parse_planilla(path):
    workers, legend = {}, {}
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for t in page.extract_tables() or []:
                if len(t) < 3:
                    continue
                # fila con los números de día (≥10 celdas numéricas)
                ni = next((i for i, row in enumerate(t)
                           if sum(1 for c in row if (c or "").strip().isdigit()) >= 10), None)
                if ni is None or ni + 1 >= len(t):
                    continue
                nums, codes = t[ni], t[ni + 1]
                name = (t[0][0] or "").replace("\n", " ").strip()
                if not name:
                    continue
                sched = {}
                for col in range(min(len(nums), len(codes))):
                    cell = (nums[col] or "").strip()
                    if cell.isdigit() and 1 <= int(cell) <= 31:
                        sched[int(cell) - 1] = (codes[col] or "").strip() or "D"
                hrs = next((v for c in reversed(codes)
                            if (v := _to_hours(c)) is not None and v > 40), None)
                workers[name] = {"schedule": sched, "hours": hrs}
            # leyenda (mapa de códigos del propio PDF)
            for line in (page.extract_text() or "").splitlines():
                m = _LEG.match(line.strip())
                if m and m.group(1) not in legend:
                    legend[m.group(1)] = m.group(2).strip()
    return {"workers": workers, "legend": legend}


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "DIEGO LABORATORIO.pdf"
    data = parse_planilla(path)
    print(f"Trabajadores leídos: {len(data['workers'])}")
    print("Leyenda (auto-detectada):")
    for code, desc in data["legend"].items():
        print(f"  {code:>4} = {desc}")
    print("\nResumen por trabajador (nº MT / horas):")
    for name, w in data["workers"].items():
        mt = sum(1 for c in w["schedule"].values() if c == "MT")
        print(f"  {name:<32} MT={mt:>2}  horas={w['hours']}")
