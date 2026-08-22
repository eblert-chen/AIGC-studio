FROM postgres@sha256:95206741a5b214807675e14165369d05b93a9cf692223b616d07cca227e74b0b

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        postgresql-16-pgaudit=16.1-2.pgdg13+1 \
    && rm -rf /var/lib/apt/lists/*
