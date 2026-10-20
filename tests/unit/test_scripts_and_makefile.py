"""Unit tests for the shell scripts and Makefile, run in dry-run mode.

Nothing here talks to Google Cloud: the scripts are run with DRY_RUN=1 and the
Makefile with `make -n`, and the printed commands are checked.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SCRIPTS = ROOT / "scripts"
PROJECT = "qwiklabs-gcp-04-e3eaf2b4f0c3"
SA_EMAIL = f"curbside-agent@{PROJECT}.iam.gserviceaccount.com"

EXPECTED_PROJECT_ROLES = {
    "roles/aiplatform.user",
    "roles/bigquery.jobUser",
    "roles/mcp.toolUser",
    "roles/serviceusage.serviceUsageConsumer",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/cloudtrace.agent",
}
# Broad roles the agent must never get.
FORBIDDEN_ROLES = {
    "roles/owner",
    "roles/editor",
    "roles/storage.admin",
    "roles/bigquery.admin",
    "roles/bigquery.dataOwner",
    "roles/bigquery.dataEditor",
}


def dry_run(script: str, **env) -> str:
    full_env = {
        **os.environ,
        "DRY_RUN": "1",
        "PROJECT_ID": PROJECT,
        "SA_NAME": "curbside-agent",
        **env,
    }
    result = subprocess.run(
        ["bash", str(SCRIPTS / script)],
        env=full_env,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def granted_roles(output: str, verb: str) -> set[str]:
    return {
        m.group(1)
        for line in output.splitlines()
        if f"projects {verb}-iam-policy-binding" in line
        for m in [re.search(r"--role=(\S+)", line)]
        if m
    }


@pytest.mark.parametrize(
    "script", ["setup_sa.sh", "destroy_sa.sh", "with_cli_metadata.sh"]
)
def test_scripts_parse(script: str) -> None:
    subprocess.run(["bash", "-n", str(SCRIPTS / script)], check=True)


@pytest.mark.parametrize("script", ["setup_sa.sh", "destroy_sa.sh"])
def test_scripts_require_project(script: str) -> None:
    env = {k: v for k, v in os.environ.items() if k not in {"PROJECT_ID", "SA_NAME"}}
    result = subprocess.run(
        ["bash", str(SCRIPTS / script)], env=env, capture_output=True, text=True
    )
    assert result.returncode != 0
    assert "PROJECT_ID is required" in result.stderr


def test_setup_creates_the_service_account() -> None:
    out = dry_run("setup_sa.sh", DEPLOYER="user:dev@example.com")
    assert "gcloud iam service-accounts create curbside-agent" in out
    assert f"--project={PROJECT}" in out


def test_setup_grants_exactly_the_expected_project_roles() -> None:
    out = dry_run("setup_sa.sh", DEPLOYER="user:dev@example.com")
    roles = granted_roles(out, "add")
    assert roles == EXPECTED_PROJECT_ROLES
    assert not roles & FORBIDDEN_ROLES
    for line in out.splitlines():
        if "add-iam-policy-binding" in line and "projects " in line:
            assert any(
                f"--member=serviceAccount:{name}@{PROJECT}.iam.gserviceaccount.com"
                in line
                for name in ("curbside-agent", "curbside-planner", "curbside-auditor")
            )


def test_setup_scopes_data_access_to_the_dataset() -> None:
    out = dry_run("setup_sa.sh", DATASET="my_ds", DEPLOYER="user:dev@example.com")
    assert (
        f"GRANT `roles/bigquery.dataViewer` ON SCHEMA `{PROJECT}.my_ds` "
        f'TO "serviceAccount:curbside-planner@{PROJECT}.iam.gserviceaccount.com"'
    ) in out


def test_setup_lets_the_deployer_act_as_the_sa() -> None:
    out = dry_run("setup_sa.sh", DEPLOYER="user:dev@example.com")
    line = next(
        line
        for line in out.splitlines()
        if "service-accounts add-iam-policy-binding" in line
        and "--role=roles/iam.serviceAccountUser" in line
    )
    assert SA_EMAIL in line
    assert "--member=user:dev@example.com" in line
    assert "--role=roles/iam.serviceAccountUser" in line


def test_setup_enables_required_apis() -> None:
    out = dry_run("setup_sa.sh", DEPLOYER="user:dev@example.com")
    enable = next(line for line in out.splitlines() if "gcloud services enable" in line)
    for api in ("aiplatform", "bigquery", "iam", "cloudbuild"):
        assert f"{api}.googleapis.com" in enable


def test_destroy_sa_removes_every_role_setup_grants() -> None:
    setup = granted_roles(dry_run("setup_sa.sh", DEPLOYER="user:x@y.z"), "add")
    destroy_out = dry_run("destroy_sa.sh")
    assert granted_roles(destroy_out, "remove") == setup
    assert "REVOKE `roles/bigquery.dataViewer`" in destroy_out
    assert f"gcloud iam service-accounts delete {SA_EMAIL}" in destroy_out


# --- Makefile ----------------------------------------------------------------

needs_make = pytest.mark.skipif(
    shutil.which("make") is None, reason="make not installed"
)


def make_n(*args: str) -> str:
    result = subprocess.run(
        ["make", "-n", "-C", str(ROOT), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


@needs_make
def test_make_deploy_runs_tests_then_deploys_with_the_sa() -> None:
    out = make_n("deploy")
    lines = out.splitlines()
    test_idx = next(i for i, line in enumerate(lines) if "pytest tests/unit" in line)
    deploy = next(line for line in lines if "agents-cli deploy" in line)
    assert test_idx < lines.index(deploy)
    assert f"--project {PROJECT}" in deploy
    assert "--region us-central1" in deploy
    assert f"--service-account {SA_EMAIL}" in deploy
    # Agent Identity would override (and clear) the service account.
    assert "--no-agent-identity" in deploy
    assert "GOOGLE_CLOUD_LOCATION=global" in deploy
    assert "CURBSIDE_MODEL=gemini-3.8-flash" in deploy
    assert f"CURBSIDE_PROJECT_ID={PROJECT}" in deploy
    assert "--no-confirm-project" in deploy
    assert "--service-name curbside" in deploy
    assert "with_cli_metadata.sh" not in deploy


@needs_make
def test_make_deploy_overrides() -> None:
    out = make_n("deploy", "REGION=us-east1", "MODEL=gemini-x", "PROJECT_ID=p1")
    deploy = next(line for line in out.splitlines() if "agents-cli deploy" in line)
    assert "--region us-east1" in deploy
    assert "CURBSIDE_MODEL=gemini-x" in deploy
    assert "CURBSIDE_PROJECT_ID=p1" in deploy
    assert "--service-account curbside-agent@p1.iam.gserviceaccount.com" in deploy


@needs_make
def test_make_deploy_dev_uses_sandbox_name_and_metadata() -> None:
    out = make_n("deploy-dev", "DEV_ID=501")
    deploy = next(line for line in out.splitlines() if "agents-cli deploy" in line)
    assert "scripts/with_cli_metadata.sh" in deploy
    assert "deployment_metadata.dev.json" in deploy
    assert "--service-name curbside-dev-501" in deploy
    assert f"--service-account {SA_EMAIL}" in deploy
    assert "--no-agent-identity" in deploy


@needs_make
def test_make_destroy_dev_targets_sandbox_metadata() -> None:
    out = make_n("destroy-dev", "DEV_ID=501")
    assert "scripts/destroy_dev.py" in out
    assert "scripts/destroy_agent.py" not in out
    assert "--name curbside-dev-501" in out
    assert "--metadata deployment_metadata.dev.json" in out


@needs_make
def test_make_destroy_targets_the_regional_agent() -> None:
    out = make_n("destroy")
    assert "scripts/destroy_agent.py" in out
    assert f"--project {PROJECT}" in out
    assert "--region us-central1" in out
    assert "--name curbside" in out
    assert "--yes" not in out  # asks by default
    assert "--yes" in make_n("destroy", "CONFIRM=yes")


@needs_make
def test_make_test_runs_unit_tests() -> None:
    assert "pytest tests/unit" in make_n("test")


@needs_make
def test_make_help_lists_every_documented_target() -> None:
    result = subprocess.run(
        ["make", "-C", str(ROOT), "help"], capture_output=True, text=True, check=True
    )
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)  # strip colours
    listed = {line.split()[0] for line in plain.splitlines() if line.startswith("  ")}
    for target in (
        "setup",
        "test",
        "deploy",
        "deploy-dev",
        "deploy-frontend",
        "destroy",
        "destroy-dev",
        "destroy-sa",
        "run",
        "dev",
        "web",
        "serve",
        "frontend",
    ):
        assert target in listed, target


@needs_make
def test_make_web_runs_adk_web_on_the_repo(tmp_path) -> None:
    out = make_n("web")
    line = next(line for line in out.splitlines() if "adk web" in line)
    assert "--env-file .env" in line
    assert "--host 127.0.0.1" in line
    assert "--port 8000" in line
    assert line.rstrip().endswith(" .")  # agents dir = repo root, app = agent/
    custom = make_n("web", "HOST=0.0.0.0", "PORT=8080")
    assert "--host 0.0.0.0 --port 8080" in custom


def test_with_cli_metadata_restores_production_file() -> None:
    prod = ROOT / "deployment_metadata.json"
    sandbox = ROOT / "deployment_metadata.dev.json"
    original = prod.read_text()
    sandbox_existed = sandbox.exists()
    sandbox_original = sandbox.read_text() if sandbox_existed else None
    marker = "sandbox-marker-do-not-commit\n"
    try:
        sandbox.write_text(marker)
        result = subprocess.run(
            [
                "bash",
                str(SCRIPTS / "with_cli_metadata.sh"),
                str(sandbox),
                "python3",
                "-c",
                "from pathlib import Path; print(Path('deployment_metadata.json').read_text(), end='')",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout == marker
        assert prod.read_text() == original
    finally:
        prod.write_text(original)
        if sandbox_existed:
            sandbox.write_text(sandbox_original or "")
        elif sandbox.exists():
            sandbox.unlink()


@needs_make
def test_make_serve_runs_the_deployed_app_locally() -> None:
    out = make_n("serve")
    line = next(line for line in out.splitlines() if "uvicorn" in line)
    assert "agent.fast_api_app:app" in line
    assert "--env-file .env" in line
    assert "--reload" in line


def test_split_identities_and_no_runtime_dataset_grant():
    out = dry_run("setup_sa.sh", DEPLOYER="user:x@y.z")
    planner = f"curbside-planner@{PROJECT}.iam.gserviceaccount.com"
    auditor = f"curbside-auditor@{PROJECT}.iam.gserviceaccount.com"
    grants = [
        line for line in out.splitlines() if "GRANT `roles/bigquery.dataViewer`" in line
    ]
    assert len(grants) == 3
    assert all(SA_EMAIL not in line for line in grants)
    assert any(planner in line and "a4i_health" in line for line in grants)
    assert not any(planner in line and "a4i_audit" in line for line in grants)
    assert any(auditor in line and "a4i_audit" in line for line in grants)
    tokens = [
        line
        for line in out.splitlines()
        if "--role=roles/iam.serviceAccountTokenCreator" in line
        and f"--member=serviceAccount:{SA_EMAIL}" in line
    ]
    assert len(tokens) == 2
    assert all("service-accounts add-iam-policy-binding" in line for line in tokens)
    assert any("REVOKE" in line and SA_EMAIL in line for line in out.splitlines())


def test_toolbox_deployment_stays_private():
    out = dry_run(
        "deploy_toolbox.sh",
        REGION="us-central1",
        TOOLBOX_IMAGE="us-docker.pkg.dev/p/r/toolbox:tested",
    )
    assert "--no-allow-unauthenticated" in out
    assert "--invoker-iam-check" in out
    assert (
        f"--service-account=curbside-planner@{PROJECT}.iam.gserviceaccount.com" in out
    )
    assert "roles/run.invoker" in out
    assert "allUsers" not in out


def test_destroy_sa_finishes_after_one_tool_identity_is_already_missing(tmp_path):
    log = tmp_path / "commands"
    gcloud = tmp_path / "gcloud"
    gcloud.write_text(
        '#!/bin/bash\nprintf "%s\\n" "$*" >> "$COMMAND_LOG"\nif [[ "$1 $2 $3" == "iam service-accounts describe" && "$4" == curbside-planner@* ]]; then exit 1; fi\n'
    )
    bq = tmp_path / "bq"
    bq.write_text("#!/bin/bash\nexit 0\n")
    gcloud.chmod(0o755)
    bq.chmod(0o755)
    result = subprocess.run(
        ["bash", str(SCRIPTS / "destroy_sa.sh")],
        env={
            **os.environ,
            "PATH": str(tmp_path) + ":" + os.environ["PATH"],
            "PROJECT_ID": PROJECT,
            "SA_NAME": "curbside-agent",
            "DRY_RUN": "0",
            "COMMAND_LOG": str(log),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    commands = log.read_text()
    assert f"iam service-accounts describe curbside-planner@{PROJECT}" in commands
    assert f"iam service-accounts delete curbside-planner@{PROJECT}" not in commands
    assert f"iam service-accounts delete {SA_EMAIL}" in commands


def test_toolbox_dry_run_creates_default_repository_before_build():
    out = dry_run("deploy_toolbox.sh", REGION="us-east1", TOOLBOX_IMAGE="")
    create = next(
        line for line in out.splitlines() if "artifacts repositories create" in line
    )
    assert "--location=us-east1" in create
    assert f"--project={PROJECT}" in create
    assert "--repository-format=docker" in create
    assert out.index(create) < out.index("gcloud builds submit")


@pytest.mark.parametrize(
    "state,success,created",
    [
        ("missing", True, True),
        ("exists", True, False),
        ("denied", False, False),
        ("wrong_format", False, False),
    ],
)
def test_toolbox_repository_preflight(tmp_path, state, success, created):
    log = tmp_path / "commands"
    gcloud = tmp_path / "gcloud"
    gcloud.write_text("""#!/bin/bash
printf '%s\\n' "$*" >> "$COMMAND_LOG"
if [[ "$1 $2 $3" == 'artifacts repositories describe' ]]; then
  case "$REPO_STATE" in
    missing) echo 'NOT_FOUND: repository does not exist' >&2; exit 1;;
    denied) echo 'PERMISSION_DENIED: lookup denied' >&2; exit 1;;
    wrong_format) echo 'NPM';;
    *) echo 'DOCKER';;
  esac
fi
""")
    gcloud.chmod(0o755)
    result = subprocess.run(
        ["bash", str(SCRIPTS / "deploy_toolbox.sh")],
        env={
            **os.environ,
            "PATH": str(tmp_path) + ":" + os.environ["PATH"],
            "PROJECT_ID": PROJECT,
            "SA_NAME": "curbside-agent",
            "REGION": "us-central1",
            "DRY_RUN": "0",
            "TOOLBOX_IMAGE": "",
            "REPO_STATE": state,
            "COMMAND_LOG": str(log),
        },
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) == success, result.stderr
    commands = log.read_text()
    assert ("artifacts repositories create" in commands) == created
    assert ("builds submit" in commands) == success
    if created:
        assert commands.index("artifacts repositories create") < commands.index(
            "builds submit"
        )
