# What This Project Actually Is

*The framing the rest of the documentation assumes.*

> Short version: `enpkg` is a **tool for building knowledge graphs**, not one particular
> knowledge graph. That distinction is what makes the `enpkg:` vocabulary the one thing in
> the repository that must not drift. Referenced from
> [VOCABULARY.md](VOCABULARY.md), [SCHEMA_REVIEW_AND_VOCABULARY_PLAN.md](SCHEMA_REVIEW_AND_VOCABULARY_PLAN.md),
> [ADDING_A_BLOCK.md](ADDING_A_BLOCK.md) and [`vocab/enpkg.ttl`](vocab/enpkg.ttl).

---

## The name is legacy; the scope is not

`enpkg` is a legacy name, carried over from the first version of the pipeline. The current
project is **not** about building one fixed "ENPKG" knowledge graph — it's a general-purpose
tool that lets *any user* build a knowledge graph for *their own* project/dataset.

## Why the vocabulary is the constant

Because many different users/projects will each generate their own KG with this pipeline, the
**ontology (the `enpkg:` vocabulary) is the one constant across all of them** — it must be
reused and stay consistent, not vary per project or per run. Every user's generated graph is
only interoperable with every other user's graph because they all commit to the same term
definitions.

This is why vocabulary work (see
[SCHEMA_REVIEW_AND_VOCABULARY_PLAN.md](SCHEMA_REVIEW_AND_VOCABULARY_PLAN.md)) treats namespace
stability and getting term definitions (domain/range/label) right as something to settle
deliberately, not something to freely rename or let drift with the serializer's implementation.

## Why the prefix keeps its legacy name

The `enpkg:` short name itself is being kept (despite the legacy-name origin) — renaming a
published vocabulary prefix is disruptive for the same reuse reason above, and the name doesn't
need to change just because the pipeline's own history does.
