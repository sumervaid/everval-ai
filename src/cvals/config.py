"""Workspace settings and storage admission checks."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    token: str | None = None
    docker_host: str | None = None
    sandbox_image: str = "python:3.12-slim"
    allow_remote_providers: bool = False
    max_request_bytes: int = 5 * 1024 * 1024

    @property
    def database_path(self) -> Path:
        return self.data_dir / "workspace.sqlite3"

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"

    def prepare(self) -> None:
        path = self.data_dir.expanduser().absolute()
        if str(path).startswith("/Volumes/"):
            volume = Path(*path.parts[:3])
            if not volume.is_mount():
                raise ValueError(f"Data volume is not mounted: {volume}")
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if shutil.disk_usage(path).free < 100 * 1024 * 1024:
            raise ValueError("Less than 100 MiB free in the workspace data volume.")
        self.artifacts_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    @classmethod
    def from_env(cls) -> Settings:
        default = Path.home() / ".local" / "share" / "custom-vals"
        return cls(
            data_dir=Path(os.getenv("CVALS_DATA_DIR", str(default))).expanduser().absolute(),
            token=os.getenv("CVALS_TOKEN") or None,
            docker_host=os.getenv("CVALS_DOCKER_HOST") or None,
            sandbox_image=os.getenv("CVALS_SANDBOX_IMAGE", "python:3.12-slim"),
            allow_remote_providers=os.getenv("CVALS_ALLOW_REMOTE_PROVIDERS", "").lower()
            in {"1", "true"},
        )
