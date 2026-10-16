"""Workspace settings and storage admission checks."""

from __future__ import annotations

import ipaddress
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


def provider_origin(value: str, *, origin_only: bool = False) -> str:
    """Canonical network origin; allowlisted entries must be exact HTTPS origins."""
    if not isinstance(value, str) or not value or any(char.isspace() for char in value):
        raise ValueError("Provider origins must be nonempty URLs without whitespace")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Provider origin has an invalid port") from exc
    hostname = parsed.hostname
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.netloc.endswith(":")
        or parsed.username is not None
        or parsed.password is not None
        or "*" in hostname
        or "%" in hostname
        or (port is not None and not 1 <= port <= 65535)
        or (
            origin_only
            and (
                parsed.scheme != "https"
                or parsed.path
                or parsed.query
                or parsed.fragment
                or "?" in value
                or "#" in value
            )
        )
    ):
        raise ValueError(
            "Internal provider entries must be exact HTTPS origins, without paths or credentials"
        )
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        hostname = hostname.encode("idna").decode("ascii").lower()
        if len(hostname) > 253 or not all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in hostname.split(".")
        ):
            raise ValueError("Provider origin has an invalid hostname") from None
    else:
        hostname = address.compressed
        if address.version == 6:
            hostname = "[" + hostname + "]"
    default_port = 443 if parsed.scheme == "https" else 80
    suffix = ":" + str(port) if port is not None and port != default_port else ""
    return parsed.scheme + "://" + hostname + suffix


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    token: str | None = None
    docker_host: str | None = None
    sandbox_image: str = "python:3.12-slim"
    allow_remote_providers: bool = False
    max_request_bytes: int = 5 * 1024 * 1024
    internal_provider_origins: tuple[str, ...] = ()
    provider_ca_bundle: Path | None = None

    def __post_init__(self) -> None:
        if type(self.internal_provider_origins) is not tuple:
            raise ValueError("Internal provider origins must be an immutable tuple")
        object.__setattr__(
            self,
            "internal_provider_origins",
            tuple(
                sorted(
                    {
                        provider_origin(value, origin_only=True)
                        for value in self.internal_provider_origins
                    }
                )
            ),
        )
        if self.provider_ca_bundle is not None:
            object.__setattr__(
                self,
                "provider_ca_bundle",
                Path(self.provider_ca_bundle).expanduser().absolute(),
            )

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
        default = Path.home() / ".local" / "share" / "everval"
        return cls(
            data_dir=Path(os.getenv("EVERVAL_DATA_DIR", os.getenv("CVALS_DATA_DIR", str(default))))
            .expanduser()
            .absolute(),
            token=os.getenv("EVERVAL_TOKEN", os.getenv("CVALS_TOKEN")) or None,
            docker_host=os.getenv("EVERVAL_DOCKER_HOST", os.getenv("CVALS_DOCKER_HOST")) or None,
            sandbox_image=os.getenv(
                "EVERVAL_SANDBOX_IMAGE", os.getenv("CVALS_SANDBOX_IMAGE", "python:3.12-slim")
            ),
            internal_provider_origins=tuple(
                value.strip()
                for value in os.getenv("EVERVAL_INTERNAL_PROVIDER_ORIGINS", "").split(",")
                if value.strip()
            ),
            provider_ca_bundle=Path(os.environ["EVERVAL_PROVIDER_CA_BUNDLE"])
            if os.getenv("EVERVAL_PROVIDER_CA_BUNDLE")
            else None,
            allow_remote_providers=os.getenv(
                "EVERVAL_ALLOW_REMOTE_PROVIDERS", os.getenv("CVALS_ALLOW_REMOTE_PROVIDERS", "")
            ).lower()
            in {"1", "true"},
        )
