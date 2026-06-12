ifeq ($(OS),Windows_NT)
VENV := .venv-win
VENV_PYTHON := $(VENV)/Scripts/python.exe
VENV_PYTEST := $(VENV)/Scripts/pytest.exe
VENV_PRECOMMIT := $(VENV)/Scripts/pre-commit.exe
VENV_DMYPY := $(VENV)/Scripts/dmypy.exe
VENV_PTW := $(VENV)/Scripts/ptw.exe
VENV_WATCHMEDO := $(VENV)/Scripts/watchmedo.exe
else
VENV := .venv
VENV_PYTHON := $(VENV)/bin/python
VENV_PYTEST := $(VENV)/bin/pytest
VENV_PRECOMMIT := $(VENV)/bin/pre-commit
VENV_DMYPY := $(VENV)/bin/dmypy
VENV_PTW := $(VENV)/bin/ptw
VENV_WATCHMEDO := $(VENV)/bin/watchmedo
endif

$(VENV_PYTHON):
	uv venv --clear --python 3.13 $(VENV)

requirements.test: $(VENV_PYTHON) requirements.test.in requirements.constraints
	uv pip compile requirements.test.in -c requirements.constraints -o requirements.test

sync: $(VENV_PYTHON) requirements.test
	uv pip sync --python $(VENV_PYTHON) --strict requirements.test

unittest:
	$(VENV_PYTHON) -m pytest -vvv

test:
	python scripts/test.py

watch-tests: sync
	$(VENV_PTW) . --now -vvv

pre-commit: sync
	$(VENV_PRECOMMIT) run -a

mypy: sync
	MYPYPATH=stubs $(VENV_DMYPY) run .

watch-mypy:
	$(VENV_WATCHMEDO) auto-restart --directory=./ --pattern="*.py;*.pyi" --no-restart-on-command-exit --recursive -- ${MAKE} mypy

integration-tests: sync
	cd integration_tests && ../$(VENV_PYTHON) test_banning_works.py

watch-integration-tests:
	$(VENV_WATCHMEDO) auto-restart --directory=./ --pattern="*.py;*.pyi;*.yaml.j2" --ignore-patterns "config/custom_components/ban_allowlist/*.py" --no-restart-on-command-exit --recursive -- ${MAKE} integration-tests

clean-integration-tests:
	git clean -fx ./integration_tests && (cd integration_tests && docker compose kill && docker compose rm -sf)

.PHONY: sync unittest test watch-tests pre-commit mypy watch-mypy integration-tests watch-integration-tests clean-integration-tests
