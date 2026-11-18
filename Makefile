PYTHON ?= python3
.PHONY: install test run docker

install:
	$(PYTHON) -m pip install -e ".[ingest,dev]"

test:
	$(PYTHON) -m pytest

run:
	$(PYTHON) -m lagrange_rx serve --host 127.0.0.1 --port 8000

docker:
	docker build -t lagrange-rx .
