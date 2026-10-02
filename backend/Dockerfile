FROM python:3.11-slim

RUN pip install --no-cache-dir uv==0.6.17

WORKDIR /app

COPY pyproject.toml ./
RUN uv sync --no-dev

COPY . .

ENV PATH="/app/.venv/bin:${PATH}"
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips=*"]
