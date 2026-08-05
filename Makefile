.PHONY: test lint shellcheck

test:
	.venv/bin/python -m pytest tests/ -q

lint:
	ansible-lint src/create_pcluster.yml src/delete_pcluster.yml

# Every tracked .sh except the Jinja2 templates, discovered rather than listed so
# a newly added shell script joins the gate without anyone remembering to add it.
# scripts/sbatch_default_submission_script.sh is excluded because it is a template
# rendered by create_pcluster.yml, not a script: raw shellcheck reports SC1009 and
# SC1072 parse errors on its `{% if %}` blocks and stops analyzing. Its *rendered*
# output is what a node runs, and tests/test_templates.py renders it.
SHELLCHECK_EXCLUDE := scripts/sbatch_default_submission_script.sh
SHELLCHECK_FILES := $(filter-out $(SHELLCHECK_EXCLUDE),$(shell git ls-files '*.sh'))

shellcheck:
	shellcheck $(SHELLCHECK_FILES)
