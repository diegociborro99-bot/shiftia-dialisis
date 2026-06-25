# Backend de Shiftia · Diálisis para Railway.
# Antes de construir, ejecuta ./prepare_deploy.sh (vendoriza shiftia-core).
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY server/requirements.txt /app/server/requirements.txt
RUN pip install --no-cache-dir -r /app/server/requirements.txt

# código del backend + módulos del guante + motor vendorizado
COPY server/ /app/server/
COPY dialisis.py pdf_reader.py analyze.py bootstrap.py /app/
COPY vendor/ /app/vendor/

EXPOSE 8770
CMD ["sh", "-c", "cd /app/server && uvicorn main:app --host 0.0.0.0 --port ${PORT:-8770}"]
