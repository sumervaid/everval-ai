# Single-host deployment templates

Use the [on-premises guide](../../docs/on-premises.md) to build an immutable release, initialize storage, and bootstrap personal authentication before installing these units.

Files:

- `everval-api.service`: loopback API with its embedded worker disabled.
- `everval-worker.service`: one durable worker for the same local workspace.
- `everval.env.example`: shared nonsecret policy; public inference disabled.
- `provider-secrets.env.example`: optional credentials, root-owned mode 0600.
- `preflight.py`: read-only startup checks; refuses missing storage, disabled team authentication, unsafe permissions, writable releases, and public-provider access.

The templates target a dedicated Linux host or VM with systemd. They are not a container sandbox, a tenant boundary, or a substitute for host egress controls. Linux service operation must be validated on the target host; portable preflight tests do not establish that systemd hardening or Docker integration works there. No deployment or service start is performed by copying these files.
