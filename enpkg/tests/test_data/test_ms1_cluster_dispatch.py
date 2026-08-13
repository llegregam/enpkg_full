"""Unit tests for cluster-aware MS1 dispatch (satellite inheritance).

Pure/fast: exercises ``inherit_satellite_annotations`` on synthetic spectra
stamped with MS1 cluster roles — no DB / matchms library involved.
"""

from enpkg.monolith.utils.ms1_cluster_dispatch import inherit_satellite_annotations


def _keys(adducts):
    """Short-InChIKey sets of each adduct's formula group, in order."""
    return [{lotus.short_inchikey for lotus in a.lotus} for a in adducts]


def _cluster(make_spectrum, make_adduct, make_recipe, make_lotus):
    """Anchor [M+H]+ (with a real + a coincidental hypothesis) + two satellites + a singleton."""
    mh = make_recipe(ingredients={"proton": 1})
    mna = make_recipe(ingredients={"sodium": 1})
    mk = make_recipe(ingredients={"potassium": 1})

    resolved_group = [make_lotus(structure_inchikey="AAAAAAAAAAAAAA-UHFFFAOYSA-N")]
    coincidental_group = [make_lotus(structure_inchikey="BBBBBBBBBBBBBB-UHFFFAOYSA-N")]

    anchor = make_spectrum(feature_id=1)
    anchor.ms1_cluster_id = 0
    anchor.ms1_cluster_role = "anchor"
    anchor.ms1_assigned_recipe = mh
    # The anchor's own annotations: its resolved [M+H]+ molecule *and* a coincidental
    # [M+Na]+ hypothesis that must NOT propagate to satellites.
    anchor.ms1_annotations = [
        make_adduct(recipe=mh, lotus=resolved_group),
        make_adduct(recipe=mna, lotus=coincidental_group),
    ]

    sat_na = make_spectrum(feature_id=2)
    sat_na.ms1_cluster_id = 0
    sat_na.ms1_cluster_role = "satellite"
    sat_na.ms1_assigned_recipe = mna

    sat_k = make_spectrum(feature_id=3)
    sat_k.ms1_cluster_id = 0
    sat_k.ms1_cluster_role = "satellite"
    sat_k.ms1_assigned_recipe = mk

    singleton = make_spectrum(feature_id=4)
    singleton.ms1_cluster_role = "singleton"  # ms1_cluster_id stays None

    return anchor, sat_na, sat_k, singleton, {"mh": mh, "mna": mna, "mk": mk}


def test_satellite_inherits_anchor_base_form_molecule(
    make_spectrum, make_adduct, make_recipe, make_lotus
):
    anchor, sat_na, sat_k, singleton, r = _cluster(
        make_spectrum, make_adduct, make_recipe, make_lotus
    )
    inherit_satellite_annotations([anchor, sat_na, sat_k, singleton])

    # Each satellite gets exactly the anchor's resolved group, under its own recipe.
    assert _keys(sat_na.ms1_annotations) == [{"AAAAAAAAAAAAAA"}]
    assert sat_na.ms1_annotations[0].recipe == r["mna"]
    assert _keys(sat_k.ms1_annotations) == [{"AAAAAAAAAAAAAA"}]
    assert sat_k.ms1_annotations[0].recipe == r["mk"]


def test_coincidental_anchor_hypothesis_is_not_inherited(
    make_spectrum, make_adduct, make_recipe, make_lotus
):
    anchor, sat_na, sat_k, singleton, _ = _cluster(
        make_spectrum, make_adduct, make_recipe, make_lotus
    )
    inherit_satellite_annotations([anchor, sat_na, sat_k, singleton])

    inherited_keys = {k for group in _keys(sat_na.ms1_annotations) for k in group}
    assert "BBBBBBBBBBBBBB" not in inherited_keys  # coincidental [M+Na]+ group excluded


def test_anchor_and_singleton_untouched(
    make_spectrum, make_adduct, make_recipe, make_lotus
):
    anchor, sat_na, sat_k, singleton, _ = _cluster(
        make_spectrum, make_adduct, make_recipe, make_lotus
    )
    inherit_satellite_annotations([anchor, sat_na, sat_k, singleton])

    assert len(anchor.ms1_annotations) == 2  # unchanged
    assert singleton.ms1_annotations == []   # never a satellite -> left alone


def test_satellite_without_anchor_gets_empty(make_spectrum, make_recipe):
    # Defensive: a satellite whose cluster has no anchor ends up with no annotations.
    orphan = make_spectrum(feature_id=9)
    orphan.ms1_cluster_id = 42
    orphan.ms1_cluster_role = "satellite"
    orphan.ms1_assigned_recipe = make_recipe(ingredients={"sodium": 1})

    inherit_satellite_annotations([orphan])
    assert orphan.ms1_annotations == []
