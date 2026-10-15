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


def test_optimization_form_excludes_unpartitioned_development_collections(tmp_path: Path) -> None:
    async def exercise() -> None:
        app = WorkbenchApp(Settings(tmp_path))
        collections = [
            {"id": "plain", "name": "Unpartitioned", "benchmark_partition": None},
            {"id": "dev", "name": "Explicit development", "benchmark_partition": "dev"},
            {"id": "holdout", "name": "Reserved holdout", "benchmark_partition": "holdout"},
        ]
        systems = [{"id": "live", "name": "Local adapter", "kind": "http", "model": "test"}]
        async with app.run_test(size=(100, 36)) as pilot:
            await app.workers.wait_for_complete()
            app.optimization_form((collections, systems))
            await pilot.pause()
            assert isinstance(app.screen, FormDialog)
            development = next(
                field for field in app.screen.fields if field.key == "development_collection_id"
            )
            assert development.options == [("Explicit development", "dev")]
            await pilot.press("escape")
            app.optimization_form(([collections[0], collections[2]], systems))
            await pilot.pause()
            assert isinstance(app.screen, EvidenceDialog)
            assert "explicitly marked development" in app.screen.text

    asyncio.run(exercise())
