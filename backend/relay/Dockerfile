FROM python:3.12-alpine@sha256:6d43704baacd1bfbe7c295d7f13079d5d8104ed33568873133f8fc69980419df AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system relay && adduser --system --ingroup relay relay
ARG INSTALL_OBS=false

COPY pyproject.toml README.md ./

RUN python -c "import subprocess,tomllib; config=tomllib.load(open('pyproject.toml','rb')); build=config.get('build-system',{}).get('requires',[]); dependencies=config['project']['dependencies']; subprocess.check_call(['pip','install',*build,*dependencies])" \
    && if [ "$INSTALL_OBS" = "true" ]; then pip install "esdk-obs-python>=3.25,<4"; fi

COPY relay_service ./relay_service
COPY alembic.ini ./
COPY alembic ./alembic

RUN pip install --no-deps --no-build-isolation .

USER relay
EXPOSE 8000

CMD ["uvicorn", "relay_service.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
