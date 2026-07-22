.PHONY: test lint shellcheck

test:
	pytest tests/ -v

lint:
	ansible-lint src/create_pcluster.yml src/delete_pcluster.yml

shellcheck:
	shellcheck performance/hpc-benchmark.sh
