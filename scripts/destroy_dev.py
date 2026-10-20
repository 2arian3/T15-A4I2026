"""Deletes a sandbox Curbside Agent Runtime. Refuses production `curbside`.

Usage (normally via `make destroy-dev`):
    uv run python scripts/destroy_dev.py --project P --region R --name curbside-dev-501
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

_AGENT = Path(__file__).with_name("destroy_agent.py")
_spec = importlib.util.spec_from_file_location("destroy_agent", _AGENT)
destroy_agent = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(destroy_agent)

PROD_DISPLAY_NAME = "curbside"
PROD_METADATA = Path("deployment_metadata.json")
DEFAULT_METADATA = Path("deployment_metadata.dev.json")
SANDBOX_PREFIX = "curbside-dev"


def is_sandbox_name(display_name: str) -> bool:
    """True for `curbside-dev` and `curbside-dev-<suffix>`."""
    return display_name == SANDBOX_PREFIX or display_name.startswith(f"{SANDBOX_PREFIX}-")


def default_sandbox_name() -> str:
    suffix = os.environ.get("DEV_ID") or str(os.getuid())
    return f"{SANDBOX_PREFIX}-{suffix}"


def refuse_reason(display_name: str, metadata_path: Path) -> str | None:
    """Why this invocation would hit production, or None if it is a sandbox."""
    if display_name == PROD_DISPLAY_NAME or not is_sandbox_name(display_name):
        return (
            f"Refusing to delete '{display_name}'. "
            f"This script only deletes '{SANDBOX_PREFIX}' or '{SANDBOX_PREFIX}-*'."
        )
    if metadata_path.name == PROD_METADATA.name:
        return (
            f"Refusing to use {metadata_path}: that is the production metadata file. "
            f"Sandbox deploys use {DEFAULT_METADATA}."
        )
    sandbox_id = destroy_agent.read_metadata_id(metadata_path)
    prod_id = destroy_agent.read_metadata_id(PROD_METADATA)
    if sandbox_id and prod_id and sandbox_id == prod_id:
        return (
            f"Refusing: {metadata_path} points at the production agent {prod_id}."
        )
    return None


def destroy_dev(
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
    reason = refuse_reason(display_name, metadata_path)
    if reason:
        out(reason)
        return 2
    return destroy_agent.destroy(
        project=project,
        region=region,
        display_name=display_name,
        metadata_path=metadata_path,
        assume_yes=assume_yes,
        client_factory=client_factory,
        input_fn=input_fn,
        out=out,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", required=True, help="Agent Runtime region")
    parser.add_argument(
        "--name",
        default=default_sandbox_name(),
        help="Sandbox display name (must be curbside-dev or curbside-dev-*)",
    )
    parser.add_argument("--metadata", default=DEFAULT_METADATA, type=Path)
    parser.add_argument("--yes", action="store_true", help="Skip confirmation")
    args = parser.parse_args(argv)
    return destroy_dev(
        project=args.project,
        region=args.region,
        display_name=args.name,
        metadata_path=args.metadata,
        assume_yes=args.yes,
        client_factory=destroy_agent._make_client,
    )


if __name__ == "__main__":
    sys.exit(main())
