FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt ./
# Select CPU wheels explicitly; Linux x86 must not pull CUDA dependencies.
RUN grep -E '^(torch|torchvision)==' requirements.txt > /tmp/requirements-cpu.txt \
    && python -m pip install --index-url https://download.pytorch.org/whl/cpu \
        -r /tmp/requirements-cpu.txt \
    && python -m pip install -r requirements.txt
COPY backend/app ./backend/app
COPY backend/ollama.toml ./backend/ollama.toml
CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
