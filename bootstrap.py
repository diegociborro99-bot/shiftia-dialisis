"""
Engancha el motor genérico shiftia-core (proyecto hermano) para poder
`import shiftiacore` desde este guante sin duplicar código.

En producción se sustituiría por una dependencia instalada (pip install shiftiacore).
"""
import os
import sys

_CORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shiftia-core")
if os.path.isdir(_CORE) and _CORE not in sys.path:
    sys.path.insert(0, _CORE)

# valida que el motor está disponible
import shiftiacore  # noqa: E402,F401
