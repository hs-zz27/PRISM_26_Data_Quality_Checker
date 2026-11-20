FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt ./
# CPU wheels by default, so Linux x86 does not pull CUDA dependencies. compose.gpu.yaml overrides this
# with the default package index, whose Linux wheels include CUDA.
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
RUN grep -E '^(torch|torchvision)==' requirements.txt > /tmp/requirements-cpu.txt \
    && python -m pip install --index-url "${TORCH_INDEX_URL}" \
        -r /tmp/requirements-cpu.txt \
    && python -m pip install -r requirements.txt
COPY backend/app ./backend/app
COPY backend/ollama.toml ./backend/ollama.toml
CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
