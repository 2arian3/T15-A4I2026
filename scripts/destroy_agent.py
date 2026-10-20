"""Deletes the Curbside agent from Agent Runtime (Agent Engine).

agents-cli has no delete command and gcloud has no Agent Runtime commands, so
this uses the Agent Platform SDK directly.

The agent is found from deployment_metadata.json (written by `agents-cli
deploy`), or, if that is missing or empty, by its display name.

Usage (normally via `make destroy`):
    uv run python scripts/destroy_agent.py --project P --region R --name curbside [--yes]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

METADATA_FILE = "deployment_metadata.json"
METADATA_KEY = "remote_agent_runtime_id"


def read_metadata_id(path: Path) -> str | None:
    """Returns the deployed agent's resource name from the metadata file, if any."""
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    value = data.get(METADATA_KEY) if isinstance(data, dict) else None
    return value or None


def clear_metadata(path: Path) -> None:
    """Resets the metadata file so a later deploy creates a fresh agent."""
    if path.exists():
        path.write_text(
            json.dumps({METADATA_KEY: None, "deployment_timestamp": None}, indent=2)
            + "\n"
        )


def find_by_display_name(client: Any, display_name: str) -> list[str]:
    """Lists resource names of Agent Runtime instances with this display name."""
    agents = client.runtimes.list(config={"filter": f'display_name="{display_name}"'})
    names = []
    for agent in agents:
        resource = agent.api_resource
        if resource is not None and resource.display_name == display_name:
            names.append(resource.name)
    return names


def resource_matches(resource_name: str, project: str, region: str) -> bool:
    """True when a resource name lives in the expected region of this project.

    The project segment may be an ID or a number, so only the region is
    checked strictly; a different project ID is rejected when it is not numeric.
    """
    parts = resource_name.split("/")
    if len(parts) != 6 or parts[0] != "projects" or parts[4] != "reasoningEngines":
        return False
    if parts[3] != region:
        return False
    return parts[1] == project or parts[1].isdigit()


def confirm(display_name: str, input_fn: Callable[[str], str] = input) -> bool:
    """Asks the user to type the agent name back before deleting."""
    try:
        typed = input_fn(f"Type the agent name '{display_name}' to delete it: ")
    except EOFError:
        return False
    return typed.strip() == display_name


def destroy(
    *,
    project: str,
    region: str,
    display_name: str,
    metadata_path: Path,
    assume_yes: bool,
    client_factory: Callable[[str, str], Any],
    input_fn: Callable[[str], str] = input,
    out: Callable[[str], None] = print,
) -> int:
    """Finds and deletes the agent. Returns a process exit code."""
    client = client_factory(project, region)

    name = read_metadata_id(metadata_path)
    if name and not resource_matches(name, project, region):
        out(
            f"{metadata_path} points at {name}, which is not in "
            f"{project}/{region}. Refusing to delete it."
        )
        return 2
    if name:
        targets = [name]
    else:
        out(f"No agent id in {metadata_path}; looking up '{display_name}' by name.")
        targets = find_by_display_name(client, display_name)

    if not targets:
        out(f"No Agent Runtime agent named '{display_name}' in {project}/{region}.")
        clear_metadata(metadata_path)
        return 0
    if len(targets) > 1:
        out("More than one agent has that display name; delete them by id:")
        for target in targets:
            out(f"  {target}")
        return 2

    target = targets[0]
    out(f"About to delete {target} (sessions and other child resources too).")
    if not assume_yes and not confirm(display_name, input_fn):
        out("Not confirmed; nothing deleted.")
        return 1

    client.runtimes.delete(name=target, force=True)
    clear_metadata(metadata_path)
    out(f"Deleted {target}.")
    return 0


def _make_client(project: str, region: str) -> Any:
    import agentplatform  # imported lazily so unit tests need no credentials

    return agentplatform.Client(project=project, location=region)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", required=True, help="Agent Runtime region")
    parser.add_argument("--name", required=True, help="Agent display name")
    parser.add_argument("--metadata", default=METADATA_FILE, type=Path)
    parser.add_argument("--yes", action="store_true", help="Skip confirmation")
    args = parser.parse_args(argv)
    return destroy(
        project=args.project,
        region=args.region,
        display_name=args.name,
        metadata_path=args.metadata,
        assume_yes=args.yes,
        client_factory=_make_client,
    )


if __name__ == "__main__":
    sys.exit(main())
