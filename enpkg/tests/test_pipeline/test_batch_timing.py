"""Tests for the batch runner's aggregate timing breakdown.

``_timing_table`` is a pure function of the per-experiment ``RunResult.durations``
maps, so these exercise it directly rather than running a batch.
"""

from enpkg.monolith.pipeline.batch_runner import BatchResult, _timing_table
from enpkg.monolith.pipeline.runner import STAGE_LOAD, STAGE_RDF, RunResult


def _batch(*duration_maps: dict[str, float]) -> BatchResult:
    return BatchResult(results=[RunResult(durations=dict(d)) for d in duration_maps])


def test_returns_nothing_when_no_durations_were_recorded():
    # A batch that failed before running anything must still yield a well-formed
    # summary, so the table contributes no lines rather than an empty header.
    assert _timing_table(_batch({}, {})) == []
    assert _timing_table(BatchResult()) == []


def test_sums_across_experiments_and_orders_by_total_descending():
    table = "\n".join(
        _timing_table(
            _batch(
                {"sirius": 100.0, "ms1": 5.0, STAGE_LOAD: 1.0},
                {"sirius": 200.0, "ms1": 5.0, STAGE_LOAD: 1.0},
            )
        )
    )
    rows = [line for line in table.splitlines() if "Sirius" in line or "MS1 enhancement" in line]
    # Sirius (300s) must precede MS1 (10s).
    assert len(rows) == 2 and "Sirius" in rows[0]
    assert "300.0" in rows[0] and "150.0" in rows[0]  # total, then mean
    assert "TOTAL" in table


def test_labels_stage_keys_and_block_ids():
    table = "\n".join(_timing_table(_batch({STAGE_LOAD: 2.0, STAGE_RDF: 3.0, "ms2": 1.0})))
    # Reserved stage keys render with their human labels, not the dunder form.
    assert "Load analysis" in table and "RDF serialization" in table
    assert "__load__" not in table and "__rdf__" not in table
    # Block ids render with the registry's label.
    assert "MS2 enhancement" in table


def test_counts_only_experiments_that_recorded_the_stage():
    # A block skipped in one experiment must not drag its mean down: n is the
    # number of experiments that actually recorded it.
    table = "\n".join(_timing_table(_batch({"sirius": 100.0}, {})))
    row = next(line for line in table.splitlines() if "Sirius" in line)
    assert "100.0" in row and row.rstrip().endswith("1")
