# CLAUDE.md

Project-specific context for Claude Code when working in this repository.

## What this project actually is

`enpkg` is a legacy name, carried over from the first version of the pipeline. The current
project is **not** about building one fixed "ENPKG" knowledge graph — it's a general-purpose tool
that lets *any user* build a knowledge graph for *their own* project/dataset.

Because many different users/projects will each generate their own KG with this pipeline, the
**ontology (the `enpkg:` vocabulary) is the one constant across all of them** — it must be reused
and stay consistent, not vary per project or per run. Every user's generated graph is only
interoperable with every other user's graph because they all commit to the same term definitions.
This is why vocabulary work (see
[docs/SCHEMA_REVIEW_AND_VOCABULARY_PLAN.md](docs/SCHEMA_REVIEW_AND_VOCABULARY_PLAN.md)) treats
namespace stability and getting term definitions (domain/range/label) right as something to settle
deliberately, not something to freely rename or let drift with the serializer's implementation.

The `enpkg:` short name itself is being kept (despite the legacy-name origin) — renaming a
published vocabulary prefix is disruptive for the same reuse reason above, and the name doesn't
need to change just because the pipeline's own history does.

## Docstrings and comments

Docstrings and comments describe the code as it is now — what it does, why it works this way,
what to watch out for. They are **not** a place to narrate changes made over time ("previously
this used X", "changed to handle Y", "used to be a single function, now split"). Change history
belongs in the commit log / changelog, not in the source text. Write every docstring and comment
as if the current state is the only state that ever existed.

## Changelog

[CHANGELOG.md](CHANGELOG.md) is a running journal for humans and for Claude to follow what has
been done and why. Add an entry there whenever a change carries a design decision, a behavioural
change, a new pipeline component, a non-obvious fix, or a reversal of an earlier decision —
newest first, under a `### YYYY-MM-DD` heading, saying what changed and why. Skip it for routine
changes (typos, formatting, no-op dependency bumps). It complements the commit log, it does not
replace it: put the "why" that a diff can't show there. This is also where the design-history
narrative that must stay out of docstrings and comments belongs.
