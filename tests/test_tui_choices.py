"""Choice controls must render their selected value and accept keyboard changes."""

import asyncio
from pathlib import Path

import pytest
from textual.widgets import Select

from cvals.config import Settings
from cvals.tui.app import WorkbenchApp
from cvals.tui.dialogs import FieldSpec, FormDialog


@pytest.mark.parametrize("size", [(80, 24), (136, 46)])
def test_choice_value_is_visible_and_keyboard_selectable(
    tmp_path: Path, size: tuple[int, int]
) -> None:
    async def exercise() -> None:
        app = WorkbenchApp(Settings(tmp_path))
        async with app.run_test(size=size) as pilot:
            await app.workers.wait_for_complete()
            app.push_screen(
                FormDialog(
                    "Choose development evidence",
                    "The selected collection must remain visible.",
                    [
                        FieldSpec(
                            "collection",
                            "Development collection",
                            kind="choice",
                            value="first",
                            options=[
                                ("First collection", "first"),
                                ("Second collection", "second"),
                            ],
                        )
                    ],
                )
            )
            await pilot.pause()
            selected = app.screen.query_one("#field-collection", Select)
            current = selected.query_one("SelectCurrent")
            assert selected.content_region.contains_region(current.content_region)
            assert "First&#160;collection" in app.export_screenshot()
            selected.focus()
            await pilot.press("enter", "down", "enter")
            await pilot.pause()
            assert selected.value == "second"
            assert "Second&#160;collection" in app.export_screenshot()

    asyncio.run(exercise())
