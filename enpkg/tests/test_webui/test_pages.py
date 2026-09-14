"""Tests for the three pages.

Everything here drives the interface the way a person would, because the per-visitor
store is only reachable inside a request: a test cannot seed it from outside, and one
that tried would be asserting on something the page never saw.

The routes are registered by the ``register_pages`` fixture in ``conftest.py``, which has
to run after the plugin clears the route table.
"""

import asyncio

import pytest
import yaml
from nicegui.testing import User

from enpkg.monolith.configuration.serializer_config import SerializerConfig
from enpkg.monolith.pipeline import config_io
from enpkg.monolith.pipeline.blocks import BLOCKS


@pytest.fixture(autouse=True)
async def _loop_for_async_handlers(user: User):
    """Restore the event loop a running server would have recorded.

    An async click handler is scheduled through ``background_tasks``, which reads that
    loop. The harness clears it while resetting NiceGUI's globals, so without this the
    handler is never scheduled and the click silently does nothing. Depending on ``user``
    is what places this after the reset.
    """
    from nicegui import core

    previous = core.loop
    core.loop = asyncio.get_running_loop()
    yield
    # Restoring matters: this loop is closed once the test ends, and leaving it in place
    # would have later tests schedule work onto a dead loop, which hangs rather than
    # raising.
    core.loop = previous


async def test_root_redirects_to_imports(user: User) -> None:
    await user.open("/")
    await user.should_see("Input data")


async def test_every_page_renders(user: User) -> None:
    for route, heading in (
        ("/imports", "Input data"),
        ("/pipeline", "Pipeline"),
        ("/serializer", "RDF serializer"),
    ):
        await user.open(route)
        await user.should_see(heading)


async def test_navigation_is_present_on_every_page(user: User) -> None:
    await user.open("/pipeline")
    await user.should_see("Imports")
    await user.should_see("Serializer")


async def test_pipeline_lists_every_registered_block(user: User) -> None:
    """The page is built from the registry, so a new block appears without touching it."""
    await user.open("/pipeline")
    for block in BLOCKS:
        await user.should_see(block.label)


async def test_imports_offers_both_modes(user: User) -> None:
    await user.open("/imports")
    await user.should_see("Single experiment")
    await user.should_see("Batch")


async def test_imports_survives_a_revisit_after_files_are_chosen(
    user: User, tmp_path, monkeypatch
) -> None:
    """Revisiting Imports must not fail on the filenames the first visit stored.

    A select rejects a value that is not among its options. The dropdowns are built
    before the folder has been scanned, so a stored filename handed to them at
    construction raises and the page returns a 500 -- which is what happened on the
    second visit, once a first visit had recorded a choice.
    """
    from enpkg.monolith.webui import paths

    data = tmp_path / "experiment"
    data.mkdir()
    (data / "arnica_pos.mgf").write_text("", encoding="utf-8")
    (data / "metadata.tsv").write_text("", encoding="utf-8")
    (data / "arnica_pos_quant.csv").write_text("", encoding="utf-8")
    monkeypatch.setattr(paths, "DEFAULT_INPUT_DIR", data)

    # First visit scans the folder and records a filename for each dropdown. Waiting for
    # the filename to appear is what makes this a real reproduction: without it the scan
    # may not have finished, nothing would be stored, and the revisit would prove nothing.
    await user.open("/imports")
    await user.should_see("arnica_pos.mgf")

    # Leaving and returning is what used to raise.
    await user.open("/pipeline")
    await user.should_see("Pipeline")
    await user.open("/imports")
    await user.should_see("arnica_pos.mgf")


async def test_pipeline_reports_an_empty_selection(user: User) -> None:
    await user.open("/pipeline")
    await user.should_see("Select at least one block")


async def test_ticking_a_block_validates_its_configuration(user: User) -> None:
    """Ticking a block should move the page from "nothing chosen" to a valid config."""
    await user.open("/pipeline")
    user.find("Molecular networking").click()
    await user.should_see("Configuration is valid")


async def test_selection_survives_navigating_away_and_back(user: User) -> None:
    """A choice lives in the visitor's store, not in the page that drew it."""
    await user.open("/pipeline")
    user.find("Molecular networking").click()
    await user.should_see("Configuration is valid")

    await user.open("/imports")
    await user.should_see("Input data")

    await user.open("/pipeline")
    await user.should_see("Configuration is valid")


async def test_serializer_page_explains_the_absent_network_option(user: User) -> None:
    """The missing option is the one a user is most likely to go looking for."""
    await user.open("/serializer")
    await user.should_see("networking block")


async def test_serializer_reset_reports_itself(user: User) -> None:
    await user.open("/serializer")
    user.find("Reset to defaults").click()
    await user.should_see("Reset to defaults.")


async def test_save_refuses_an_empty_selection(user: User) -> None:
    await user.open("/pipeline")
    user.find(marker="save-button").click()
    await user.should_see("Select at least one block")


async def test_run_refuses_before_inputs_are_chosen(user: User) -> None:
    """The run button checks what the Imports page was supposed to supply."""
    await user.open("/pipeline")
    user.find("Molecular networking").click()
    await user.should_see("Configuration is valid")
    user.find(marker="run-button").click()
    await user.should_see("Imports page")


async def _choose_config(user: User, path) -> None:
    """Point the Imports page at a config file.

    `type()` appends to whatever the box holds, so the default has to go first. The
    picker only records a path that names an existing file, which is why every caller
    creates the file before typing it.
    """
    await user.open("/imports")
    user.find(marker="config-path").clear().type(str(path))


async def test_loading_a_config_replaces_the_selection(user: User, tmp_path) -> None:
    """Loading is a full replace, including blocks the file does not mention.

    It is also the round trip that matters: Load writes to the store on one page, and
    the Pipeline page has to build its widgets from that when it is next opened.
    """
    path = tmp_path / "config.yaml"
    configs = config_io.build_configs(["network"], {"network": {}})
    config_io.save_unified_yaml(path, configs, serializer=SerializerConfig())

    # Start from a different selection so the load has something to overwrite.
    await user.open("/pipeline")
    user.find("Sirius").click()
    await user.should_see("Configuration is valid")

    await _choose_config(user, path)
    user.find(marker="config-load").click()
    await user.should_see("Loaded")

    await user.open("/pipeline")
    await user.should_see("Configuration is valid")


async def test_loading_a_file_without_a_selection_says_so(user: User, tmp_path) -> None:
    """Section names cannot stand in for the selection, so the page refuses to guess."""
    path = tmp_path / "legacy.yaml"
    path.write_text(yaml.safe_dump({"network": {}}), encoding="utf-8")

    await _choose_config(user, path)
    user.find(marker="config-load").click()
    await user.should_see("no block selection")


async def test_loading_a_missing_file_reports_it(user: User, tmp_path) -> None:
    """An absent file reads as an empty config, which would silently clear everything."""
    await user.open("/imports")
    user.find(marker="config-path").clear().type(str(tmp_path / "absent.yaml"))
    user.find(marker="config-load").click()
    await user.should_see("Could not read")


async def test_saving_produces_a_file_the_command_line_accepts(
    user: User, tmp_path
) -> None:
    """What the page writes has to be what `enpkg run` can read back."""
    path = tmp_path / "out.yaml"
    path.write_text(yaml.safe_dump({"selected_blocks": []}), encoding="utf-8")

    await _choose_config(user, path)

    await user.open("/pipeline")
    user.find("Molecular networking").click()
    await user.should_see("Configuration is valid")
    user.find(marker="save-button").click()
    await user.should_see("Saved")

    data = config_io.load_unified_yaml(path)
    assert config_io.get_selection(data) == ["network"]
    assert config_io.get_serializer_section(data) != {}
