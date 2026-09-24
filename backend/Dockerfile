FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY . ./backend

EXPOSE 8001

CMD ["uvicorn", "backend.backend_main:app", "--host", "0.0.0.0", "--port", "8001"]
