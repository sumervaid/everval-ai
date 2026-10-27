"""The branded API and legacy integrations share one implementation."""

import subprocess
import sys

import cvals
import everval
from cvals.judge_calibration import JudgeCalibration


def test_public_api_keeps_class_identity():
    assert everval.__version__ == cvals.__version__
    assert everval.JudgeCalibration is JudgeCalibration


def test_module_entry_point():
    result = subprocess.run(
        [sys.executable, "-m", "everval", "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.stdout.strip() == everval.__version__


def test_import_and_version_do_not_load_optional_application_surfaces():
    script = (
        "import everval, sys; "
        "assert 'cvals.api' not in sys.modules; "
        "assert 'cvals.cli' not in sys.modules; "
        "assert 'cvals.judge_calibration' not in sys.modules; "
        "assert 'inspect_ai' not in sys.modules; "
        "from everval.cli import main; "
        "sys.argv = ['everval', '--version']; main(); "
        "assert 'cvals.cli' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], check=True, capture_output=True, text=True, timeout=30
    )
    assert result.stdout.strip() == everval.__version__
