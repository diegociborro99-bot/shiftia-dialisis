"""
Engancha el motor genérico shiftia-core para poder `import shiftiacore` desde
este guante sin duplicar código. Busca el motor en, por orden:

  1. vendor/shiftiacore        ← copia vendorizada dentro de ESTE repo (Docker/checkout)
  2. ../shiftia-core           ← proyecto hermano (desarrollo local)
  3. $SHIFTIA_CORE_PATH        ← ruta explícita por variable de entorno

En producción se sustituiría por una dependencia instalada (pip install shiftiacore).
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_CANDIDATES = (
    os.path.join(_HERE, "vendor"),                 # motor vendorizado (este repo)
    os.path.join(_HERE, "..", "shiftia-core"),     # repo hermano (local)
    os.environ.get("SHIFTIA_CORE_PATH", ""),       # ruta explícita
)
_seen = {os.path.abspath(p) for p in sys.path}
for _p in _CANDIDATES:
    if not _p:
        continue
    _abs = os.path.abspath(_p)
    # el motor es el paquete 'shiftiacore' DENTRO de la ruta candidata
    if os.path.isdir(os.path.join(_abs, "shiftiacore")) and _abs not in _seen:
        sys.path.insert(0, _abs)
        _seen.add(_abs)

# valida que el motor está disponible
import shiftiacore  # noqa: E402,F401
