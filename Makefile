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
	@files="$$( { git ls-files '*.py'; find mcp_server tests src -name '*.py' -not -path '*/__pycache__/*'; } | sort -u )"; \
	if [ -z "$$files" ]; then echo "make lint: no Python files found" >&2; exit 1; fi; \
	out="$$(.venv/bin/python -m pyflakes $$files 2>&1 | grep 'undefined name' || true)"; \
	if [ -n "$$out" ]; then \
		echo "$$out"; \
		echo "make lint: an undefined name is a guaranteed NameError -- fix before committing" >&2; \
		exit 1; \
	fi; \
	echo "make lint: no undefined names in $$(echo "$$files" | wc -l | tr -d ' ') Python files"

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
