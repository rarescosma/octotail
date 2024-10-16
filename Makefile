default: .venv/freeze

.venv/freeze:
	test -f .venv/bin/activate || python3 -mvenv .venv
	. .venv/bin/activate && poetry install --no-dev && pip freeze > .venv/freeze

install:
	chmod 755 $(PWD)/octotail/main.py
	ln -sf $(PWD)/octotail/main.py /usr/local/bin/octotail

bump:
	bumpversion patch --verbose pyproject.toml uv.lock --commit --sign-tags --tag

pycheck:
	./hacks/pycheck-parallel || ./hacks/pycheck-fix
