"""Resource-limited PDF extraction child; never imports project task content."""

from __future__ import annotations

import io
import json
import resource
import sys
from typing import Any


def main() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (6, 6))
    # RLIMIT_AS is reliable on Linux; macOS reserves a large virtual address range.
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (384 * 1024 * 1024, 384 * 1024 * 1024))
    from pypdf import PdfReader, filters

    # Bound decompression before text extraction on all platforms.
    for key in (
        "MAX_DECLARED_STREAM_LENGTH",
        "MAX_ARRAY_BASED_STREAM_OUTPUT_LENGTH",
        "JBIG2_MAX_OUTPUT_LENGTH",
        "LZW_MAX_OUTPUT_LENGTH",
        "RUN_LENGTH_MAX_OUTPUT_LENGTH",
        "ZLIB_MAX_OUTPUT_LENGTH",
        "FLATE_MAX_BUFFER_SIZE",
    ):
        if hasattr(filters, key):
            setattr(filters, key, 2 * 1024 * 1024)

    data = sys.stdin.buffer.read(5 * 1024 * 1024 + 1)
    if len(data) > 5 * 1024 * 1024:
        raise ValueError("PDF exceeds 5 MiB")
    reader = PdfReader(io.BytesIO(data), strict=True)
    if reader.is_encrypted:
        raise ValueError("Encrypted PDFs are not supported")
    if len(reader.pages) > 100:
        raise ValueError("PDF exceeds 100 pages")
    sources: list[dict[str, Any]] = []
    total = 0
    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        total += len(text)
        if total > 1_000_000:
            raise ValueError("PDF text exceeds one million characters")
        sources.append({"id": f"page-{index}", "page": index, "text": text})
    if not any(source["text"].strip() for source in sources):
        raise ValueError("PDF has no extractable text; OCR is not available")
    print(
        json.dumps(
            {
                "pages": len(sources),
                "sources": sources,
                "text": "\n\n".join(source["text"] for source in sources),
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"error": str(exc)[:500]}))
