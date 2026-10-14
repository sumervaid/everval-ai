# Workbench interface

A guided React application for the local Everval AI service. Start with the repository’s [quickstart](../README.md) for the complete application.

## Development

Use Node.js 24.15+ LTS, 22.22.2+, or 26+.

```sh
npm ci
npm run dev
```

Vite serves the development interface on loopback and proxies `/api` requests to the local service at `http://127.0.0.1:8765`. Run `cvals serve` in a second terminal.

```sh
npm run typecheck
npm test
npm run build
```

The production build is written to `dist/` and served by the Python application. It uses no external fonts, images, analytics, or CDN dependencies.

## Workflows

- **Overview:** inspect recorded activity and install original starter collections.
- **Collections:** author scenarios, preview document and data sources, import JSON/YAML, inspect evaluator criteria, validate, and export public task inputs.
- **Systems:** configure compatible model endpoints using environment variable references. Reference and incomplete fixture controls are explicitly distinguished from models.
- **Experiments:** freeze a task cohort and system configuration, run repeated trials, inspect check evidence, compare intervals and costs, cancel, rerun, and export JSON/CSV/HTML.
- **Import results:** grade externally produced submissions against an exact collection hash. Missing usage remains unknown.
- **Settings:** inspect local storage and service capabilities, and find the command-line companion.

The interface stores a configured workspace token only in session storage. Provider keys are never entered or persisted in the browser. The service is authoritative for validation, execution policy, budget admission, and all reported metrics.

## Interface structure

`src/api.ts` implements the same-origin request boundary. Typed service contracts live in `src/types.ts`; page modules own their loading and mutation states. Hash navigation keeps the static application independent of server rewrite rules. Provider calls and evaluation never run in the browser.

Tests cover token handling, multipart boundaries, missing metrics, experiment protocol submission, fixture labeling, and admission errors.
