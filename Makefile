.PHONY: test lint shellcheck

test:
	.venv/bin/python -m pytest tests/ -q

# This target used to run ansible-lint over src/create_pcluster.yml and
# src/delete_pcluster.yml. Both were deleted: nothing in the toolkit executes an
# Ansible playbook any more, so the target had no files left and would have
# passed on nothing. It runs the one Python gate this repo actually commits to
# instead -- the undefined-name sweep whose rationale is in
# tests/test_undefined_names.py. Broader pyflakes output (unused locals,
# placeholder-free f-strings) is deliberately NOT gated: clearing that backlog
# is a separate decision, and a gate nobody can keep green stops being read.
# Tracked Python plus every .py under mcp_server/, tests/ and src/, matching
# tests/test_undefined_names.py's own file set: the doc-hygiene tests are
# gitignored and mcp_server/ was untracked for a while, so a git-only sweep
# skips exactly the newest code.
lint:
	@# Two gates, narrowest first. The undefined-name sweep predates ruff and
	@# stays because it is the one this repo committed to after a NameError
	@# reached a live build; ruff's F821 covers the same ground, and keeping
	@# both means the promise survives a config edit.
	@files="$$(git ls-files '*.py')"; \
	if [ -z "$$files" ]; then echo "make lint: no Python files found" >&2; exit 1; fi; \
	out="$$(.venv/bin/python -m pyflakes $$files 2>&1 | grep 'undefined name' || true)"; \
	if [ -n "$$out" ]; then \
		echo "$$out"; \
		echo "make lint: an undefined name is a guaranteed NameError -- fix before committing" >&2; \
		exit 1; \
	fi; \
	echo "make lint: no undefined names in $$(echo $$files | wc -w | tr -d ' ') files"
	@# Never --fix here: several entry points import names they do not use so
	@# tests can reach them through the module, and autofix deletes exactly
	@# that. They are declared in __all__; tests/test_reexports_survive_autofix.py
	@# fails if a new unprotected one appears.
	@.venv/bin/python -m ruff check .

# Every tracked .sh except the Jinja2 templates, discovered rather than listed so
# a newly added shell script joins the gate without anyone remembering to add it.
# scripts/sbatch_default_submission_script.sh is excluded because it is a template
# rendered by core_create_cluster, not a script: raw shellcheck reports SC1009 and
# SC1072 parse errors on its `{% if %}` blocks and stops analyzing. Its *rendered*
# output is what a node runs, and tests/test_templates.py renders it.
SHELLCHECK_EXCLUDE := scripts/sbatch_default_submission_script.sh
SHELLCHECK_FILES := $(filter-out $(SHELLCHECK_EXCLUDE),$(shell git ls-files '*.sh'))

shellcheck:
	shellcheck $(SHELLCHECK_FILES)
