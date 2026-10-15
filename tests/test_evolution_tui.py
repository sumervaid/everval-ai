import asyncio
from pathlib import Path

from textual.widgets import Static

from cvals.config import Settings
from cvals.tui import WorkbenchApp
from cvals.tui.dialogs import EvidenceDialog, FormDialog


def test_continuous_and_research_views_work_in_small_terminal(tmp_path: Path) -> None:
    async def exercise() -> None:
        app = WorkbenchApp(Settings(tmp_path))
        async with app.run_test(size=(80, 24)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.press("f11")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app.page == "autopilot"
            assert app.rows == []
            assert str(app.query_one("#page-title", Static).render()) == "Autopilot"
            await pilot.press("f12")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app.page == "lab"
            assert app.rows == []
            app.command("budget")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen, EvidenceDialog)
            await pilot.press("escape")
            app.command("catalog")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen, EvidenceDialog)
            await pilot.press("escape")
            app.command("packs")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app.page == "packs"
            app.command("pack_stage")
            await pilot.pause()
            assert isinstance(app.screen, FormDialog)

    asyncio.run(exercise())
