PREFIX ?= /usr/local

default: .venv/freeze

.venv/freeze:
	test -f .venv/bin/activate || python3 -mvenv .venv
	. .venv/bin/activate && pip install . && pip freeze > .venv/freeze

install:
	chmod 755 $(PWD)/octotail/main.py
	ln -sf $(PWD)/octotail/main.py $(PREFIX)/bin/octotail
	ln -sf $(PWD)/octotail/x.py $(PREFIX)/bin/octotailx

bump:
	bumpversion patch --verbose octotail/__init__.py uv.lock --commit --sign-tags --tag

pycheck:
	./hacks/pycheck-fmt && ./hacks/pycheck-parallel

cov:
	pytest tests --cov && coverage html
