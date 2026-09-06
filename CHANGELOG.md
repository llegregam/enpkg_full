# Changelog

A running log of notable changes to the pipeline — what changed and, more importantly,
**why**. This is a working journal for humans and for Claude Code to follow what has been
done and understand the design decisions made along the way. It is not release notes tied
to version numbers.

## How to use this file

- Add an entry whenever you make a change worth remembering: a design decision, a
  behavioural change, a new pipeline component, a non-obvious fix, or a reversal of an
  earlier decision.
- Newest entries go at the top, under a date heading (`### YYYY-MM-DD`).
- Each entry is a short bullet list. Say what changed and why. If the change overturns an
  earlier decision, name that decision and explain what prompted the change.
- Routine changes (typos, formatting, dependency bumps with no behavioural effect) don't
  need an entry — the commit log already covers those.
- This complements the commit log; it does not replace it. Focus each entry on the "why"
  that a diff can't show.

## Entries

### 2026-09-06

- Added this changelog. Rationale, design decisions, and the history of how the pipeline
  got to its current shape now live here rather than in docstrings and code comments,
  which should only describe the code as it currently is (see `CLAUDE.md`).
