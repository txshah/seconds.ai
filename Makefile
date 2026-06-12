PY  = .venv/bin/python
PIP = .venv/bin/pip
UV  = .venv/bin/uvicorn

.PHONY: help install db bootstrap ingest serve stats clean

help:
	@echo "make install    - install python deps into .venv"
	@echo "make db         - run local ClickHouse server (foreground)"
	@echo "make bootstrap  - create database + tables"
	@echo "make ingest     - run one scrape -> store cycle"
	@echo "make serve      - run the handoff API on :8000 (docs at /docs)"
	@echo "make stats      - print pipeline stats"

install:
	$(PIP) install -r requirements.txt

# Local, Docker-free ClickHouse. For Cloud, skip this and set CLICKHOUSE_* in .env.
db:
	clickhouse server --config-file=$(CURDIR)/.local/clickhouse/config.xml

bootstrap:
	$(PY) -c "from app.db import bootstrap; bootstrap(); print('schema ready')"

ingest:
	$(PY) -m app.pipeline

# Guild+Firecrawl acquisition path, run locally (needs FIRECRAWL_API_KEY in .env).
firecrawl:
	$(PY) scripts/run_firecrawl.py

serve:
	$(UV) app.api:app --host 0.0.0.0 --port 8000 --reload

stats:
	$(PY) -c "from app.api import stats; print(stats().model_dump_json(indent=2))"
