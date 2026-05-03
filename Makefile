.PHONY: test replay benchmark demo

test:
	pytest -q

replay:
	./scripts/replay_minimal.sh .replay_tmp

benchmark:
	python3 benchmarks/factor_pipeline_benchmark.py

demo:
	robin-vnext replay run --dry-run
