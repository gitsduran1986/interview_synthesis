# interview_synthesis

Expert interview transcripts (ITSM platform evaluation) and a structured split of them.

## Layout

```
raw/                     original .docx transcripts, untouched
structured/              generated markdown, one dir per interview section
  INDEX.md               interviewee + section map
  01-interview-introduction/
    expert-1.md
    expert-2.md
    expert-3.md
  02-current-environment/
  03-vendor-selection-decision-criteria/
  04-competitive-comparison/
  05-implementation-integration/
  06-cost-total-cost-of-ownership/
  07-platform-satisfaction-loyalty/
  08-switching-dynamics/
  09-interview-wrap-up/
scripts/build_structured.py   regenerates structured/ from raw/
```

Sections are section-first rather than interview-first so an evaluation agent can
read one directory and see every interviewee's answers to that section together:

```
structured/04-competitive-comparison/*.md
```

Each file carries frontmatter (`expert`, `role`, `platform`, `section`,
`section_slug`, `source`) and the full Q/A turns with timestamps, so answers stay
paired with the interviewer's question.

Section names are canonical across interviews. The one heading that varies by
interviewee — "Current ServiceNow / BMC Helix / Freshservice Environment" — is
normalized to `02-current-environment`, with the platform recorded in frontmatter.

## Regenerating

```
python3 scripts/build_structured.py
```

`structured/` is rebuilt from scratch each run; edit the script, not the output.
