"""Install the compiled web interface into the Python package."""

import shutil
from pathlib import Path

root = Path(__file__).resolve().parent.parent
source = root / "web" / "dist"
target = root / "src" / "cvals" / "static"
if not (source / "index.html").is_file():
    raise SystemExit("Build the web interface first: cd web && npm ci && npm run build")
if target.exists():
    shutil.rmtree(target)
shutil.copytree(source, target)
print(f"Installed web assets in {target}")
