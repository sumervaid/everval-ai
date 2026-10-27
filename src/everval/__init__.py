"""Public Python interface for Everval AI, loaded when an integration needs it."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from cvals import __version__

if TYPE_CHECKING:
    from cvals.config import Settings as Settings
    from cvals.inspect_export import export_inspect as export_inspect
    from cvals.inspect_export import load_inspect_dataset as load_inspect_dataset
    from cvals.judge_calibration import JudgeCalibration as JudgeCalibration
    from cvals.judge_calibration import write_report as write_judge_report
    from cvals.judge_calibration_models import CalibrationProtocol as CalibrationProtocol
    from cvals.judge_reference import CollectionProvenance as CollectionProvenance
    from cvals.judge_reference import export_study_references as export_study_references
    from cvals.judge_reference import verify_reference_bundle as verify_reference_bundle
    from cvals.models import Collection as Collection
    from cvals.models import ExperimentCreate as ExperimentCreate
    from cvals.models import Scenario as Scenario
    from cvals.models import System as System

_EXPORTS = {
    "Settings": ("cvals.config", "Settings"),
    "export_inspect": ("cvals.inspect_export", "export_inspect"),
    "load_inspect_dataset": ("cvals.inspect_export", "load_inspect_dataset"),
    "JudgeCalibration": ("cvals.judge_calibration", "JudgeCalibration"),
    "write_judge_report": ("cvals.judge_calibration", "write_report"),
    "CalibrationProtocol": ("cvals.judge_calibration_models", "CalibrationProtocol"),
    "CollectionProvenance": ("cvals.judge_reference", "CollectionProvenance"),
    "export_study_references": ("cvals.judge_reference", "export_study_references"),
    "verify_reference_bundle": ("cvals.judge_reference", "verify_reference_bundle"),
    "Collection": ("cvals.models", "Collection"),
    "ExperimentCreate": ("cvals.models", "ExperimentCreate"),
    "Scenario": ("cvals.models", "Scenario"),
    "System": ("cvals.models", "System"),
}

__all__ = [
    "__version__",
    "Settings",
    "export_inspect",
    "load_inspect_dataset",
    "JudgeCalibration",
    "write_judge_report",
    "CalibrationProtocol",
    "CollectionProvenance",
    "export_study_references",
    "verify_reference_bundle",
    "Collection",
    "ExperimentCreate",
    "Scenario",
    "System",
]


def __getattr__(name: str) -> Any:
    try:
        module, attribute = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(import_module(module), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
