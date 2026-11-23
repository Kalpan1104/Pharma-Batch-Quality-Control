# make help -> list commands. Cloud commands read REGION/SERVICE below.
REGION  ?= us-east1
SERVICE ?= batchguard
TF       = terraform -chdir=infra/terraform
PY       = python

.PHONY: help venv test train train-recent serve oq-local traffic-local monitor-local \
        tf-init tf-plan tf-apply github-setup url health traffic traffic-supplier traffic-sensor \
        monitor-run report audit revisions tf-destroy

help:            ## list commands
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-17s %s\n", $$1, $$2}'

# ---------------- local ----------------
venv:            ## create .venv with the pinned dependencies (Python 3.12)
	python3.12 -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt && pip install --no-deps -e .

test:            ## unit, contract, parity and OQ tests
	pytest

train:           ## train + validate into artifacts/ (fails if not releasable)
	$(PY) -m batchguard.train

train-recent:    ## retrain with recent batches from a new supplier
	$(PY) -m batchguard.data --recent new_supplier
	$(PY) -m batchguard.train --recent data/recent.jsonl --out artifacts_recent

serve:           ## run the API on http://localhost:8080
	PREDICTION_SINK=jsonl:logs/predictions.jsonl gunicorn --bind :8080 --threads 4 "batchguard.app:create_app()"

oq-local:        ## operational qualification against the local server
	$(PY) -m batchguard.oq --url http://localhost:8080

traffic-local:   ## send 200 batches to the local server
	$(PY) scripts/send_batches.py --url http://localhost:8080 --n 200

monitor-local:   ## monitoring report over the local prediction log
	PREDICTION_SINK=jsonl:logs/predictions.jsonl $(PY) -m batchguard.monitor

# ---------------- Google Cloud ----------------
tf-init:         ## download the Terraform provider
	$(TF) init

tf-plan:         ## show what Terraform will create
	$(TF) plan

tf-apply:        ## create or update all cloud infrastructure
	$(TF) apply

github-setup:    ## copy Terraform outputs to GitHub variables + create the approval environment
	bash scripts/github_setup.sh

url:             ## service URL
	@gcloud run services describe $(SERVICE) --region=$(REGION) --format='value(status.url)'

health:          ## call /healthz on the live service
	@curl -s -H "Authorization: Bearer $$(gcloud auth print-identity-token)" "$$(gcloud run services describe $(SERVICE) --region=$(REGION) --format='value(status.url)')/health"; echo

traffic:         ## 300 normal batches to the live service
	$(PY) scripts/send_batches.py --region $(REGION) --n 300

traffic-supplier: ## 300 batches made with a new supplier's material
	$(PY) scripts/send_batches.py --region $(REGION) --n 300 --scenario new_supplier

traffic-sensor:  ## 300 batches from a drifting temperature probe
	$(PY) scripts/send_batches.py --region $(REGION) --n 300 --scenario sensor_drift

monitor-run:     ## run the monitoring Cloud Run Job now and wait for it
	gcloud run jobs execute $(SERVICE)-monitor --region=$(REGION) --wait
	@$(MAKE) --no-print-directory report

report:          ## print the latest monitoring report
	@gcloud storage cat "$$($(TF) output -json github_variables | python3 -c 'import json,sys;print(json.load(sys.stdin)["REPORTS_URI"])')/monitoring/latest.md"

audit:           ## last 20 audit-log entries
	@bq query --use_legacy_sql=false --format=pretty \
	  "SELECT ts, event, revision, model_version, actor FROM \`$$($(TF) output -json github_variables | python3 -c 'import json,sys;print(json.load(sys.stdin)["AUDIT_TABLE"])')\` ORDER BY ts DESC LIMIT 20"

revisions:       ## which revision gets which share of traffic
	@gcloud run services describe $(SERVICE) --region=$(REGION) --format='table(status.traffic[].revisionName,status.traffic[].percent,status.traffic[].tag)'

tf-destroy:      ## delete everything Terraform created
	$(TF) destroy
