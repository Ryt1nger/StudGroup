FROM python:3.13-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir uv==0.11.12
COPY pyproject.toml uv.lock /app/
COPY src /app/src
RUN uv sync --frozen --no-dev --no-editable && useradd --uid 10001 --create-home studgroup
ENV PATH="/app/.venv/bin:$PATH"
COPY alembic.ini /app/
COPY migrations /app/migrations
USER studgroup
EXPOSE 8000
CMD ["uvicorn", "studgroup.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
