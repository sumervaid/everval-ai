"""Check repository-local Markdown links without network access or extra dependencies."""

from __future__ import annotations

import html
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
INLINE_LINK = re.compile(r'!?\[[^\]\n]*\]\(\s*(<[^>]+>|[^)\s]+)(?:\s+["\'][^\n]*?["\'])?\s*\)')
REFERENCE = re.compile(r"^\s{0,3}\[[^\]\n]+\]:\s*(<[^>]+>|\S+)", re.MULTILINE)
HTML_LINK = re.compile(r"""<(?:a|img)\b[^>]*\b(?:href|src)=["']([^"']+)["']""", re.IGNORECASE)
HTML_ANCHOR = re.compile(r"""<(?:a|h[1-6])\b[^>]*\b(?:id|name)=["']([^"']+)["']""", re.IGNORECASE)


def without_code(text: str) -> str:
    """Keep line positions while ignoring fenced examples."""
    result = []
    fence = ""
    for line in text.splitlines(keepends=True):
        match = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if fence:
            if match and match[1][0] == fence[0] and len(match[1]) >= len(fence):
                fence = ""
            result.append("\n" if line.endswith("\n") else "")
        elif match:
            fence = match[1]
            result.append("\n" if line.endswith("\n") else "")
        else:
            result.append(line)
    return "".join(result)


def anchors(text: str) -> set[str]:
    """Recognize GitHub heading slugs and explicit anchors in our Markdown."""
    cleaned = without_code(text)
    found = set(HTML_ANCHOR.findall(cleaned))
    counts: dict[str, int] = {}
    for line in cleaned.splitlines():
        heading = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if not heading:
            continue
        label = re.sub(r"!?\[([^\]]+)\]\([^)]*\)", r"\1", heading[1])
        label = html.unescape(re.sub(r"<[^>]*>", "", label)).lower()
        slug = re.sub(r"[^\w\- ]", "", label).replace(" ", "-")
        number = counts.get(slug, 0)
        counts[slug] = number + 1
        found.add(f"{slug}-{number}" if number else slug)
    return found


def destinations(text: str) -> list[tuple[int, str]]:
    cleaned = without_code(text)
    # Inline code may contain example link syntax. Keep newlines for diagnostics.
    cleaned = re.sub(r"(`+)([^\n]*?)\1", lambda match: " " * len(match[0]), cleaned)
    links = []
    for pattern in (INLINE_LINK, REFERENCE, HTML_LINK):
        for match in pattern.finditer(cleaned):
            value = html.unescape(match[1].strip("<>"))
            links.append((cleaned.count("\n", 0, match.start()) + 1, value))
    return links


def main() -> int:
    inventory = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    documents = sorted(
        {ROOT / name for name in inventory.split("\0") if name.lower().endswith(".md")}
    )
    failures = []
    checked = 0
    anchor_cache: dict[Path, set[str]] = {}
    for document in documents:
        if not document.is_file():
            continue  # Deleted files can remain in the index before staging.
        for line, value in destinations(document.read_text(encoding="utf-8")):
            parsed = urlsplit(value)
            if parsed.scheme or parsed.netloc:
                continue
            checked += 1
            target = (document.parent / unquote(parsed.path)).resolve() if parsed.path else document
            reason = ""
            if not target.is_relative_to(ROOT):
                reason = "target leaves the repository"
            elif not target.exists():
                reason = "target does not exist"
            elif parsed.fragment and target.suffix.lower() == ".md":
                if target not in anchor_cache:
                    anchor_cache[target] = anchors(target.read_text(encoding="utf-8"))
                if unquote(parsed.fragment) not in anchor_cache[target]:
                    reason = "heading or anchor does not exist"
            if reason:
                failures.append(f"{document.relative_to(ROOT)}:{line}: {value} — {reason}")
    if failures:
        print("\n".join(failures))
        print(f"Failed: {len(failures)} broken links among {checked} local destinations.")
        return 1
    print(f"Passed: {checked} local links in {len(documents)} Markdown files.")
    print("External URLs and rendered layout are not checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
