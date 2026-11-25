"""
Shared command-line arguments for the stage runner scripts in this folder.

Every stage script must be told which project (and, where relevant, which
drawing) to process. There is deliberately no default project: a default
silently ran later stages against an older project in the past.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from uuid import UUID

BACKEND_DIR = Path(__file__).resolve().parent
PROJECTS_DIR = BACKEND_DIR / "data" / "projects"


def uuid_arg(value: str) -> str:
    """argparse type: a canonical UUID string."""
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise argparse.ArgumentTypeError(f"not a valid UUID: {value!r}") from None


def add_project_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--project-id",
        required=True,
        type=uuid_arg,
        help="Project ID to process (required; there is no default project).",
    )


def add_drawing_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--drawing-id",
        required=True,
        type=uuid_arg,
        help="Drawing ID within the project (required).",
    )


def project_folder(project_id: str) -> Path:
    folder = PROJECTS_DIR / project_id
    if not folder.is_dir():
        raise SystemExit(f"Project folder not found: {folder}")
    return folder
