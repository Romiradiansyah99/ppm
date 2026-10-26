FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY src /app/src
COPY app /app/app
COPY registry /app/registry
COPY app_specs /app/app_specs
COPY .streamlit /app/.streamlit

RUN useradd --create-home --uid 10001 ppm \
    && mkdir -p /app/exports /app/incoming \
    && chown -R ppm:ppm /app
USER ppm

EXPOSE 8501
CMD ["sh", "-c", "python -m ppm.cli init-db && streamlit run app/main.py --server.address=0.0.0.0 --server.port=8501 --server.headless=true"]
