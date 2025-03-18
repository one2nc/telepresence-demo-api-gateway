FROM python:3.12.9-alpine3.20 AS base

WORKDIR /app

COPY requirements.txt .

RUN pip install uv \
    && uv pip install --system -r requirements.txt

COPY ./src .

EXPOSE 8080

CMD ["sh","-c", "uvicorn app:app --host 0.0.0.0 --port 8080"]