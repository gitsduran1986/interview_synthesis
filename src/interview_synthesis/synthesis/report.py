"""Build the standalone report page.

`ui/index.html` is the source of truth for markup and behaviour. In development it loads
`ui/data.js` beside it; here that one `<script src>` is swapped for the data inlined, so the
result is a single file with no sibling assets, no server, and no upload — something you can
mail to someone who does not have the repo.

Only what the page actually renders goes in. The page reads `grain === 'thread'` and nothing
else, so question-grain cells and columns are left out: they are the same interviewee text a
third time over, and nothing in the document points at them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DATA_TAG = '<script src="data.js" onerror="window.SYNTHESIS_MISSING = true"></script>'


def view_payload(document: dict[str, Any]) -> dict[str, Any]:
    """Drop what the page never reads, keeping every id it might follow resolvable.

    Thread cells are what the matrix renders and section cells are what the section-grain
    syntheses cite, so both stay. Question-grain rows are referenced by nothing.
    """
    trimmed = dict(document)
    trimmed["cells"] = [c for c in document.get("cells", []) if c.get("grain") != "question"]
    trimmed["columns"] = [c for c in document.get("columns", []) if c.get("grain") != "question"]
    return trimmed


def embed(payload: dict[str, Any]) -> str:
    """JSON as a JS literal, safe to sit inside a <script> block.

    `</` has to be broken up or the first one inside a quote would close the script element
    early; the line separators are escaped because they are legal JSON but have bitten older
    parsers as raw characters.
    """
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return (
        text.replace("</", "<\\/")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def build(
    synthesis_path: Path,
    template_path: Path,
    out_path: Path,
    *,
    trim: bool = True,
) -> dict[str, Any]:
    """Write a self-contained HTML report. Returns a small summary for the caller to print."""
    document = json.loads(synthesis_path.read_text())
    payload = view_payload(document) if trim else document

    template = template_path.read_text()
    if DATA_TAG not in template:
        raise RuntimeError(
            f"{template_path} no longer contains the data.js script tag the report builder "
            f"replaces. Update DATA_TAG in synthesis/report.py to match the template."
        )

    page = template.replace(
        DATA_TAG,
        "<script>\n/* synthesis document, inlined by synthesis/report.py */\n"
        f"window.SYNTHESIS = {embed(payload)};\n</script>",
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page)

    counts = document.get("run", {}).get("counts", {})
    return {
        "path": out_path,
        "bytes": len(page.encode()),
        "cells": len(payload.get("cells", [])),
        "cells_dropped": len(document.get("cells", [])) - len(payload.get("cells", [])),
        "interviewees": counts.get("rows"),
        "findings": counts.get("findings"),
    }
