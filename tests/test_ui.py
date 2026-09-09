"""The UI prototype, executed against real synthesis output.

Runs the page's own JavaScript in node with a stub DOM, so a broken field path or a crashing
render is caught here rather than in a browser during a demo. Skipped if node is absent.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from interview_synthesis import paths

ROOT = Path(__file__).resolve().parent.parent
UI = paths.ui_template()                 # ships inside the package
SYNTH = ROOT / "out/synthesis.json"
REPORT = ROOT / "report.html"

pytestmark = pytest.mark.skipif(
    not (UI.exists() and SYNTH.exists() and shutil.which("node")),
    reason="needs node and a synthesis run",
)

HARNESS = r"""
const fs = require('fs');
const js = fs.readFileSync(process.argv[2], 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
const DATA = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const el = () => ({ hidden: false, innerHTML: '', textContent: '',
  classList: { add(){}, remove(){}, toggle(){} }, addEventListener(){},
  querySelectorAll: () => [], scrollIntoView(){}, children: [], dataset: {}, click(){} });
global.document = { getElementById: el, querySelectorAll: () => [], createElement: el, body: el() };
global.window = { scrollTo(){} };
global.fetch = () => Promise.reject(new Error('offline'));
const src = js.replace(/^fetch\('\.\.\/out\/synthesis\.json'\)[\s\S]*?catch\(\(\) => \{\}\);/m, '');
const api = new Function(src +
  '; return { matrixView, findingsView, peopleView, openColumn, countCitations, setData: d => { DATA = d; } };')();
api.setData(DATA);
const out = { matrix: api.matrixView(), findings: api.findingsView(), people: api.peopleView(),
              citations: api.countCitations(), columnErrors: [] };
for (const c of DATA.columns.filter(c => c.grain === 'thread')) {
  try { api.openColumn(c.column_id); } catch (e) { out.columnErrors.push(c.column_id + ': ' + e.message); }
}
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    harness = tmp_path_factory.mktemp("ui") / "harness.cjs"
    harness.write_text(HARNESS)
    result = subprocess.run(
        ["node", str(harness), str(UI), str(SYNTH)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def doc():
    return json.loads(SYNTH.read_text())


# The "loads with no server, no upload" guarantee now lives on the built report - see
# test_report_renders_without_any_network below. The dev page's data.js was dropped when the
# report became the artifact.


def test_every_column_detail_renders(rendered):
    """The detail panel touches most fields, so this is the broadest field-path check."""
    assert rendered["columnErrors"] == []


def test_matrix_shows_every_topic(rendered, doc):
    threads = [c for c in doc["columns"] if c["grain"] == "thread"]
    assert threads
    for column in threads:
        assert column["column_id"] in rendered["matrix"]


def test_matrix_labels_empty_cells_rather_than_leaving_them_blank(rendered):
    """A blank square reads as 'no opinion'. It has to say why it is empty."""
    assert 'class="none"' in rendered["matrix"]
    assert "not asked" in rendered["matrix"]


def test_matrix_flags_conflicts(rendered):
    assert "tag-conflict" in rendered["matrix"]


def test_findings_and_people_render(rendered, doc):
    for finding in doc["findings"]:
        assert finding["kind"] in rendered["findings"]
    for case in doc["cases"]:
        row = next(r for r in doc["rows"] if r["row_id"] == case["row_id"])
        assert row["display_name"] in rendered["people"]


def test_citation_count_matches_the_document(rendered, doc):
    counted = 0
    def walk(value):
        nonlocal counted
        if isinstance(value, dict):
            if value.get("quote") and value.get("cell_id"):
                counted += 1
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk([doc["columns_synthesis"], doc["cases"], doc["findings"]])
    assert rendered["citations"] == counted


def test_nothing_leaks_undefined_into_the_page(rendered):
    for view in ("matrix", "findings", "people"):
        assert "undefined" not in rendered[view]
        assert "[object Object]" not in rendered[view]


# --------------------------- the built report ---------------------------

REPORT = ROOT / "report.html"

# Renders the built page the way a browser would: run each inline <script> in order,
# with fetch rigged to throw so any reintroduced network dependency fails loudly.
REPORT_HARNESS = r"""
const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const el = () => ({ hidden: false, innerHTML: '', textContent: '',
  classList: { add(){}, remove(){}, toggle(){} }, addEventListener(){},
  querySelectorAll: () => [], scrollIntoView(){}, children: [], dataset: {}, click(){} });
const app = el(), stats = el(), drop = el();
global.document = {
  getElementById: id => (id === 'app' ? app : id === 'stats' ? stats : id === 'drop' ? drop : el()),
  querySelectorAll: () => [], createElement: el, body: el(),
};
global.window = { scrollTo(){} };
global.fetch = () => { throw new Error('the built report must not fetch anything'); };
for (const s of scripts) new Function('window', s)(global.window);
console.log(JSON.stringify({
  scripts: scripts.length, dropHidden: drop.hidden, appVisible: app.hidden === false,
  stats: stats.innerHTML, cells: global.window.SYNTHESIS.cells.length,
  grains: [...new Set(global.window.SYNTHESIS.cells.map(c => c.grain))].sort(),
}));
"""


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    assert REPORT.exists(), "synthesize / synth-pass should have built report.html"
    harness = tmp_path_factory.mktemp("report") / "h.cjs"
    harness.write_text(REPORT_HARNESS)
    result = subprocess.run(
        ["node", str(harness), str(REPORT)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_report_is_a_single_self_contained_file():
    """The point of the artifact: mailable, no server, no sibling assets."""
    import re

    html = REPORT.read_text()
    external = re.findall(r'(?:src|href)="(?!data:)([^"]+)"', html)
    assert external == [], f"report should reference no external assets, found {external}"


def test_report_renders_without_any_network(report):
    assert report["dropHidden"] is True
    assert report["appVisible"] is True
    assert "quotes verified" in report["stats"]


def test_report_keeps_every_citation(report, doc):
    """Trimming may drop unread rows, but never the evidence count the header claims."""
    counted = 0

    def walk(value):
        nonlocal counted
        if isinstance(value, dict):
            if value.get("quote") and value.get("cell_id"):
                counted += 1
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk([doc["columns_synthesis"], doc["cases"], doc["findings"]])
    assert f"{counted} quotes verified" in report["stats"]


def test_report_drops_only_rows_nothing_points_at(report, doc):
    from interview_synthesis.synthesis.report import view_payload

    payload = view_payload(doc)
    assert report["grains"] == ["section", "thread"], "question-grain cells are unread"
    assert report["cells"] < len(doc["cells"])

    kept = {c["cell_id"] for c in payload["cells"]}
    referenced = set()

    def walk(value):
        if isinstance(value, dict):
            if value.get("cell_id"):
                referenced.add(value["cell_id"])
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk([doc["columns_synthesis"], doc["cases"], doc["findings"]])
    assert referenced <= kept, f"report dropped cited cells: {sorted(referenced - kept)[:5]}"


def test_embed_cannot_break_out_of_the_script_tag():
    from interview_synthesis.synthesis.report import embed

    hostile = embed({"a": "</script><img onerror=x>", "b": "a b c"})
    assert "</script>" not in hostile
    assert " " not in hostile and " " not in hostile


def test_builder_fails_loudly_if_the_template_changes(tmp_path):
    """A silent no-op here would ship a report with no data in it."""
    from interview_synthesis.synthesis import report as report_mod

    template = tmp_path / "t.html"
    template.write_text("<html><body>no data tag here</body></html>")
    with pytest.raises(RuntimeError, match="data.js script tag"):
        report_mod.build(SYNTH, template, tmp_path / "out.html")
