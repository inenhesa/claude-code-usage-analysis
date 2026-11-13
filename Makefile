.PHONY: test simulate analyze-fixture benchmark-plan

test:
	python3 -m pytest -q

simulate:
	python3 -m cc_usage_study.simulate --out outputs

analyze-fixture:
	python3 -m cc_usage_study.analyze fixtures/synthetic/projects --split-date 2026-10-01 --sessions

benchmark-plan:
	python3 -m cc_usage_study.benchmark --task median_bug --percents 95,25 --output outputs/benchmark-plan.json
