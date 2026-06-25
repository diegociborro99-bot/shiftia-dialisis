#!/bin/bash
# Copia (vendoriza) el motor shiftia-core dentro de esta carpeta para que la
# imagen de Docker sea autocontenida. Ejecútalo antes de desplegar / hacer push.
set -e
cd "$(dirname "$0")"
echo "Vendorizando shiftia-core → vendor/shiftiacore …"
rm -rf vendor/shiftiacore
mkdir -p vendor
cp -r ../shiftia-core/shiftiacore vendor/shiftiacore
echo "Listo."
