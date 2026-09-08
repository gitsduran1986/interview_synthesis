#!/usr/bin/env python3
"""Split the raw .docx interview transcripts into per-section markdown files.

Layout produced under structured/:

    structured/<NN-section-slug>/<expert-slug>.md

Section-first so an evaluation agent can point at one directory and read every
interviewee's answers to that section side by side.

Usage: python3 scripts/build_structured.py
"""

import re
import shutil
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw"
OUT = ROOT / "structured"

# Canonical section order. Keys are the heading text as it appears in the docx
# (lowercased); "current * environment" is matched separately because the
# platform name varies by interviewee.
SECTIONS = [
    ("01-interview-introduction", "Interview Introduction"),
    ("02-current-environment", "Current Environment"),
    ("03-vendor-selection-decision-criteria", "Vendor Selection & Decision Criteria"),
    ("04-competitive-comparison", "Competitive Comparison"),
    ("05-implementation-integration", "Implementation & Integration"),
    ("06-cost-total-cost-of-ownership", "Cost & Total Cost of Ownership"),
    ("07-platform-satisfaction-loyalty", "Platform Satisfaction & Loyalty"),
    ("08-switching-dynamics", "Switching Dynamics"),
    ("09-interview-wrap-up", "Interview Wrap-up"),
]
BY_HEADING = {title.lower(): slug for slug, title in SECTIONS}
CURRENT_ENV = re.compile(r"^current\s+(.*?)\s+environment$", re.I)

SPEAKER = re.compile(r"^(AI Interviewer|Expert\s*\d+)\s+(\d{2}:\d{2}:\d{2})$")
# Expert 1's title paragraph runs the label and the role together, e.g.
# "Expert 1Senior Director, IT Infrastructure and Operations at ...".
TITLE_RUNON = re.compile(r"^(Expert\s*\d+)(?=[A-Z])")


def paragraphs(docx_path):
    root = ET.fromstring(zipfile.ZipFile(docx_path).read("word/document.xml"))
    for p in root.iter(f"{{{W}}}p"):
        text = "".join(t.text or "" for t in p.iter(f"{{{W}}}t")).strip()
        if text:
            yield text


def section_slug(text):
    """Return (slug, platform) if this paragraph is a section heading."""
    key = text.lower().strip()
    if key in BY_HEADING:
        return BY_HEADING[key], None
    m = CURRENT_ENV.match(key)
    if m:
        return "02-current-environment", text.strip()[len("Current "):-len(" Environment")]
    return None, None


def parse(docx_path):
    """-> dict with expert label, role, platform, and {slug: [turns]}."""
    meta = {"label": None, "role": None, "platform": None}
    sections = {}
    current = None
    speaker = timestamp = None
    preamble = []

    for text in paragraphs(docx_path):
        slug, platform = section_slug(text)
        if slug:
            current = slug
            sections.setdefault(current, [])
            if platform:
                meta["platform"] = platform
            continue

        m = SPEAKER.match(text)
        if m:
            speaker, timestamp = m.group(1).strip(), m.group(2)
            if not meta["label"] and speaker.lower().startswith("expert"):
                meta["label"] = speaker
            continue

        if current is None:
            preamble.append(text)
            continue

        if speaker is None:
            # Stray text inside a section with no speaker attributed.
            sections[current].append((None, None, text))
        else:
            sections[current].append((speaker, timestamp, text))
            speaker = timestamp = None

    for line in preamble:
        m = TITLE_RUNON.match(line)
        if m:
            meta["label"] = meta["label"] or m.group(1)
            rest = line[m.end():].strip()
            if rest:
                meta["role"] = rest
        elif re.fullmatch(r"Expert\s*\d+", line):
            meta["label"] = meta["label"] or line
        elif meta["role"] is None:
            meta["role"] = line

    return meta, sections


def expert_slug(label):
    return label.lower().replace(" ", "-")


def render(meta, slug, title, turns, source):
    fm = [
        "---",
        f"expert: {meta['label']}",
        f"role: {meta['role']}",
        f"platform: {meta['platform'] or 'n/a'}",
        f"section: {title}",
        f"section_slug: {slug}",
        f"source: raw/{source}",
        "---",
        "",
        f"# {title} — {meta['label']}",
        "",
        f"*{meta['role']}*",
        "",
        "---",
        "",
    ]
    body = []
    for spk, ts, text in turns:
        if spk is None:
            body.append(f"{text}\n")
        else:
            body.append(f"**{spk}** ({ts})\n\n{text}\n")
    return "\n".join(fm) + "\n".join(body)


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    titles = dict(SECTIONS)
    index = {}

    for docx in sorted(RAW.glob("*.docx")):
        meta, sections = parse(docx)
        if not meta["label"]:
            raise SystemExit(f"could not identify expert in {docx.name}")
        who = expert_slug(meta["label"])
        index[who] = (meta, sorted(sections))
        for slug, turns in sections.items():
            d = OUT / slug
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{who}.md").write_text(
                render(meta, slug, titles[slug], turns, docx.name)
            )
            print(f"wrote {slug}/{who}.md  ({len(turns)} turns)")

    lines = [
        "# Structured transcripts",
        "",
        "Generated by `scripts/build_structured.py` from the .docx files in `raw/`.",
        "Do not edit by hand — rerun the script instead.",
        "",
        "One directory per interview section, one file per interviewee inside it, so a",
        "section can be evaluated across all interviewees at once.",
        "",
        "## Interviewees",
        "",
        "| File | Expert | Role | Platform |",
        "| --- | --- | --- | --- |",
    ]
    for who, (meta, _) in sorted(index.items()):
        lines.append(
            f"| `{who}.md` | {meta['label']} | {meta['role']} | {meta['platform'] or 'n/a'} |"
        )
    lines += ["", "## Sections", "", "| Directory | Section | Interviewees |", "| --- | --- | --- |"]
    for slug, title in SECTIONS:
        present = sorted(w for w, (_, s) in index.items() if slug in s)
        lines.append(f"| `{slug}/` | {title} | {', '.join(present) or '—'} |")
    (OUT / "INDEX.md").write_text("\n".join(lines) + "\n")
    print("wrote INDEX.md")


if __name__ == "__main__":
    main()
