"""Models for the framework matrix and its synthesis.

Structure follows Gale et al.'s Framework Method: rows are cases (interviewees), columns are
codes (questions, and their parent sections), cells hold what one case said about one code.

Two rules shape everything here:

* **Cells are verbatim.** The model's job is synthesis - agreement, conflict, pattern - not
  restating a 38-word answer in different words. Charting is a mechanical projection, so a
  reader is never separated from the interviewee's own words by a paraphrase.
* **Every object is addressable.** A UI has to navigate section -> question -> cell -> quote
  -> source turn, so each object carries a stable id and references resolve by id.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"

# Three levels. The THREAD is the comparable unit: the 74 questions are conversational
# drill-downs, not independent codes, and treating them as columns makes the matrix look
# 58% dense when the interviewees actually covered nearly everything. Grouping each
# question under the nearest preceding question that two or more people answered gives 30
# threads at 93% density with no single-source column. Sections group threads; questions
# remain as leaves so nothing is lost and a reader can still reach the exact probe.
Grain = Literal["question", "thread", "section"]
GRAIN_PREFIX = {"question": "q", "thread": "t", "section": "s"}

CellStatus = Literal[
    "answered",        # the interviewee addressed this code
    "not_asked",       # never put to them - a gap in the interview
    "no_answer",       # asked, nothing substantive came back - a gap in the evidence
    "disfluent_only",  # the only coded turn is process talk ("can you repeat the question?")
]


# --------------------------- ids ---------------------------


def cell_id(grain: Grain, column_id: str, row_id: str) -> str:
    return f"cell:{GRAIN_PREFIX[grain]}:{column_id}:{row_id}"


def column_synthesis_id(grain: Grain, column_id: str) -> str:
    return f"syn:{GRAIN_PREFIX[grain]}:{column_id}"


def case_synthesis_id(row_id: str) -> str:
    return f"syn:case:{row_id}"


# --------------------------- stage 6: the matrix ---------------------------


class SourceRef(BaseModel):
    """Pointer back to one transcript turn. The UI's deep link."""

    text_id: str
    unit_id: str
    turn_index: int
    word_count: int


class MatrixCell(BaseModel):
    """What one case said about one code. Verbatim; built without a model."""

    cell_id: str
    grain: Grain
    column_id: str
    row_id: str
    status: CellStatus
    text: str = Field(default="", description="Verbatim interviewee words, turns in order.")
    word_count: int = 0
    sources: list[SourceRef] = Field(default_factory=list)
    is_disfluent: bool = False
    assent_only: bool = Field(
        default=False,
        description="The cell's entire content is assent to the interviewer's own "
        "restatement ('Yes.', 'Yeah, well said.'). Verbatim expert speech, but the "
        "substance came from the interviewer - so it can never supply a quote.",
    )

    @property
    def filled(self) -> bool:
        return self.status == "answered"

    @property
    def quotable(self) -> bool:
        """Whether a canonical quote may be drawn from this cell."""
        return self.filled and not self.is_disfluent and not self.assent_only


class MatrixRow(BaseModel):
    """A case. Carries what the UI needs to label an axis without a second lookup."""

    row_id: str
    display_name: str | None = None
    ui_statement: str = ""
    organization: str | None = None
    role_title: str = ""


class MatrixColumn(BaseModel):
    """A code. `comparable` is what stops a reader over-reading a single-source column."""

    column_id: str
    grain: Grain
    label: str
    section_id: str
    order: int = 0
    respondent_count: int = 0
    comparable: bool = False
    is_reask: bool = False
    # Thread columns only: the questions folded into this thread, anchor first.
    anchor_question_id: str | None = None
    member_question_ids: list[str] = Field(default_factory=list)


# --------------------------- stage 7: synthesis ---------------------------


class Citation(BaseModel):
    """A verbatim span from one cell. Checked against that cell before it is accepted."""

    quote: str = Field(
        min_length=8,
        max_length=500,
        description="Copied VERBATIM from the cell's text, character for character.",
    )
    cell_id: str = Field(description="The cell this quote comes from, copied exactly.")
    row_id: str = Field(description="Whose words these are.")


class Position(BaseModel):
    """One case's stance on a column."""

    row_id: str
    stance: Literal["supports", "qualifies", "opposes", "not_addressed"]
    gist: str = Field(
        max_length=300,
        description="One line stating this interviewee's position. Not a paraphrase of "
        "their whole answer - the reader can see that verbatim in the cell.",
    )
    citation: Citation | None = None


class Agreement(BaseModel):
    statement: str = Field(max_length=400, description="What they agree on.")
    positions: list[Position] = Field(
        min_length=2, description="Two or more interviewees. Never one."
    )
    strength: Literal["explicit", "compatible", "weak"] = Field(
        description="explicit: they say the same thing. compatible: consistent but not "
        "stated as agreement. weak: a stretch worth flagging as such."
    )


class Conflict(BaseModel):
    statement: str = Field(max_length=400, description="What is actually in dispute.")
    positions: list[Position] = Field(
        min_length=2, description="Two or more genuinely opposed interviewees."
    )
    nature: Literal["factual", "evaluative", "contextual"] = Field(
        description="factual: incompatible claims about the world. evaluative: same facts, "
        "different judgement. contextual: both true in their own setting."
    )
    explains_it: str | None = Field(
        default=None,
        max_length=200,
        description="What accounts for the difference, e.g. company size. Null if unclear.",
    )


class ColumnSynthesis(BaseModel):
    """Reading DOWN a column: what everyone said about one code."""

    synthesis_id: str = ""
    grain: Grain = "question"
    column_id: str = ""
    headline: str = Field(
        max_length=500, description="The answer to 'what did they say about this?'"
    )
    canonical_quote: Citation | None = Field(
        default=None,
        description="The single best exemplar for this column - SELECTED from a cell, never "
        "written. Null if no quote captures it.",
    )
    agreements: list[Agreement] = Field(default_factory=list, max_length=6)
    conflicts: list[Conflict] = Field(default_factory=list, max_length=6)
    outliers: list[Position] = Field(
        default_factory=list,
        max_length=4,
        description="A case that stands apart from the others here.",
    )
    notable: list[str] = Field(
        default_factory=list,
        max_length=6,
        description="Anything else worth a reader's attention that is not an agreement, "
        "conflict, or outlier.",
    )
    gaps: list[str] = Field(
        default_factory=list,
        max_length=4,
        description="Who was not asked or did not answer, and what that costs the comparison.",
    )
    # Derived in Python from respondent_count; never asked of the model.
    comparability: Literal["all_rows", "partial", "single_source", "none"] = "none"


class CaseSynthesis(BaseModel):
    """Reading ACROSS a row: one case's account, kept whole."""

    synthesis_id: str = ""
    row_id: str = ""
    through_line: str = Field(
        max_length=600, description="What holds this interviewee's account together."
    )
    internal_tensions: list[Conflict] = Field(
        default_factory=list,
        max_length=4,
        description="Where this interviewee contradicts themselves. Positions here are all "
        "the same person at different moments.",
    )
    distinctive: list[str] = Field(
        default_factory=list,
        max_length=6,
        description="What only this case contributes to the corpus.",
    )
    citations: list[Citation] = Field(default_factory=list, max_length=8)


class Finding(BaseModel):
    """Cross-cutting reading of the whole matrix."""

    finding_id: str = ""
    kind: Literal["pattern", "typology", "tension", "gap", "outlier"]
    statement: str = Field(max_length=600)
    grounded_in: list[str] = Field(
        default_factory=list, description="synthesis_ids this rolls up from."
    )
    citations: list[Citation] = Field(default_factory=list, max_length=6)
    confidence: Literal["well_evidenced", "suggestive", "single_source"] = Field(
        description="With a handful of interviews, never imply a statistical result."
    )


# --------------------------- agent output wrappers ---------------------------


class ColumnSynthesisBatch(BaseModel):
    """One entry per column given to the agent, in order."""

    columns: list[ColumnSynthesis] = Field(min_length=1)


class CaseSynthesisOut(BaseModel):
    case: CaseSynthesis


class FindingsOut(BaseModel):
    findings: list[Finding] = Field(default_factory=list, max_length=12)


# --------------------------- the document the UI binds to ---------------------------


class StageUsage(BaseModel):
    stage: str
    calls: int = 0
    cached_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None


class RunMetadata(BaseModel):
    schema_version: str = SCHEMA_VERSION
    status: Literal["complete", "partial"] = "complete"
    generated_at: datetime
    model: str
    corpus_digest: str = ""
    codebook_digest: str = ""
    prompt_digests: dict[str, str] = Field(default_factory=dict)
    git_sha: str | None = None
    usage: list[StageUsage] = Field(default_factory=list)
    total_cost_usd: float | None = None
    wall_clock_s: float = 0.0
    warnings: list[str] = Field(default_factory=list)
    counts: dict[str, Any] = Field(default_factory=dict)


class Synthesis(BaseModel):
    """`out/synthesis.json` is this, serialized."""

    run: RunMetadata
    rows: list[MatrixRow] = Field(default_factory=list)
    columns: list[MatrixColumn] = Field(default_factory=list)
    cells: list[MatrixCell] = Field(default_factory=list)
    columns_synthesis: list[ColumnSynthesis] = Field(default_factory=list)
    cases: list[CaseSynthesis] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)

    # ---- UI lookups ----

    def cell(self, grain: Grain, column_id: str, row_id: str) -> MatrixCell | None:
        target = cell_id(grain, column_id, row_id)
        return next((c for c in self.cells if c.cell_id == target), None)

    def column(self, column_id: str, grain: Grain = "question") -> MatrixColumn | None:
        return next(
            (c for c in self.columns if c.column_id == column_id and c.grain == grain), None
        )

    def synthesis_for(self, column_id: str, grain: Grain = "question") -> ColumnSynthesis | None:
        target = column_synthesis_id(grain, column_id)
        return next((s for s in self.columns_synthesis if s.synthesis_id == target), None)

    def case(self, row_id: str) -> CaseSynthesis | None:
        return next((c for c in self.cases if c.row_id == row_id), None)

    def columns_in(self, section_id: str, grain: Grain = "question") -> list[MatrixColumn]:
        return sorted(
            (c for c in self.columns if c.grain == grain and c.section_id == section_id),
            key=lambda c: c.order,
        )

    def grid(self, grain: Grain) -> list[list[MatrixCell | None]]:
        """Render-ready: one list per column, in row order."""
        columns = sorted(
            (c for c in self.columns if c.grain == grain), key=lambda c: (c.section_id, c.order)
        )
        return [
            [self.cell(grain, column.column_id, row.row_id) for row in self.rows]
            for column in columns
        ]
