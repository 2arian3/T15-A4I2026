# Curbside: build, deploy and tear down

Curbside is the ADK agent for this challenge. It decides where the mobile clinic van
parks, and explains why. It runs on **Agent Runtime** (formerly Agent Engine), calls
**Gemini through Vertex AI**, and runs as its own **service account**. Everything is
driven from the `Makefile`; run `make help` for the full list.

| Setting | Default | Override |
|---|---|---|
| Project | `qwiklabs-gcp-04-e3eaf2b4f0c3` | `PROJECT_ID=` |
| Agent Runtime region | `us-central1` (Agent Runtime has no `global` location) | `REGION=` |
| Gemini location | `global` | `MODEL_LOCATION=` |
| Model | `gemini-3.8-flash` | `MODEL=` |
| Agent name | `curbside` | `AGENT_NAME=` |
| Service account | `curbside-agent@<project>.iam.gserviceaccount.com` | `SA_NAME=` |

See [the outreach pipeline guide](docs/outreach-planner.md) for the MCP server,
audit loader, identity split and request format. Cloud setup and deployment require approval.

## First time

Needs `gcloud`, `uv` and `make` on your machine.

```bash
make install     # agents-cli + Python deps
make auth        # gcloud login + application-default credentials
make env         # writes .env (Vertex AI, this project, global)
bash scripts/load.sh   # loads the a4i_health dataset, if not done already
make setup       # APIs, service account, roles (safe to re-run)
```

## Day to day

```bash
make test                     # unit tests, no credentials needed
make run Q='{"county_fips":"13121","month":"2026-11","saturdays":["2026-11-07","2026-11-14","2026-11-21","2026-11-28"]}'
make web                      # ADK web UI at http://127.0.0.1:8000/dev-ui/
make web PORT=8080 HOST=0.0.0.0   # e.g. in Cloud Shell, then Web Preview
make serve                    # the server Agent Engine runs, locally, auto-reload
make dev                      # agents-cli playground (same UI, via agents-cli)
make deploy TOOLBOX_URL=https://... # unit tests, then create/update the agent (5-10 min)
make status                   # if the deploy timed out locally, it is still running
make test-remote Q="Hello"    # ask the deployed agent
```

`make deploy` records the production agent's id in `deployment_metadata.json`. Commit it so
teammates update the same agent instead of creating a second one.

`make deploy-dev` writes `deployment_metadata.dev.json` (gitignored) and restores the
production metadata afterwards, so a sandbox deploy cannot overwrite `curbside`.
Reuse the same sandbox on the next `make deploy-dev`; pass `DEV_ID=123` to pick a
different suffix.

## Tear down

```bash
make destroy                  # deletes production curbside (asks you to type its name)
make destroy CONFIRM=yes      # no prompt
make destroy-dev              # deletes sandbox curbside-dev-<uid> only
make destroy-dev CONFIRM=yes  # same, no prompt
make destroy-sa               # removes the service account and its role grants
make destroy-all              # both, agent first
```

## The service account

`make setup` (`scripts/setup_sa.sh`) creates `curbside-agent` and grants:

| Role | Scope | Why |
|---|---|---|
| `roles/aiplatform.user` | project | Gemini on Vertex AI, Agent Runtime sessions |
| `roles/bigquery.jobUser` | project | Run queries |
| `roles/iam.serviceAccountTokenCreator` | Planner and auditor identities only | Use isolated data tools |
| `roles/mcp.toolUser` | project | Call BigQuery's managed MCP server |
| `roles/serviceusage.serviceUsageConsumer` | project | Use the project's APIs |
| `roles/logging.logWriter`, `roles/monitoring.metricWriter`, `roles/cloudtrace.agent` | project | Logs, metrics, traces |

The runtime has no direct dataset viewer grant. `curbside-planner` reads only
`a4i_health`; `curbside-auditor` reads both source datasets.

It also gives you `roles/iam.serviceAccountUser` on the account, which `make deploy` needs
to run the agent as it. If the dataset didn't exist yet when you ran `make setup`, run it
again after `scripts/load.sh`.

The deploy passes `--no-agent-identity`: Agent Identity would otherwise replace the service
account with a per-agent principal.
