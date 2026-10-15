"""P8.6-A: dclab-client and dclab-mcp build as honest wheels (scripts/check_packages_build.py)."""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path

import pytest

from scripts import check_packages_build as packages


def _fake_wheel(tmp_path: Path, metadata: str, files: dict[str, str]) -> Path:
    wheel = tmp_path / "dclab_client-0.2.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("dclab_client-0.2.0.dist-info/METADATA", metadata)
        archive.writestr("dclab_client-0.2.0.dist-info/entry_points.txt", "[console_scripts]\n")
        for name, body in files.items():
            archive.writestr(name, body)
    return wheel


def test_metadata_check_rejects_license_claims_exact_pins_tests_and_missing_fields(tmp_path):
    metadata = (
        "Metadata-Version: 2.4\nName: dclab-client\nVersion: 0.2.0\nLicense-Expression: MIT\n"
        "Classifier: License :: OSI Approved :: MIT License\nRequires-Dist: httpx==0.27.0\n\n"
    )
    wheel = _fake_wheel(tmp_path, metadata, {"dclab_client/__init__.py": "", "dclab_client/tests/test_x.py": ""})
    problems = packages.check_metadata(wheel, "dclab_client")
    for expected in (
        "missing Summary",
        "missing Requires-Python",
        "README is not the long description",
        "missing the 'Private :: Do Not Upload' guard while the license is undecided",
        "claims a license: License-Expression",
        "claims a license: License :: OSI Approved :: MIT License",
        "exact pin in a published dependency: httpx==0.27.0",
        "missing console script dclab-cli",
        "packages a test file: dclab_client/tests/test_x.py",
    ):
        assert expected in problems


def test_both_packages_build_install_and_answer_help():
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is not on PATH")
    with tempfile.TemporaryDirectory(prefix="dclab-pkg-test-") as tmp:
        work = Path(tmp)
        try:
            wheels = [packages.build_wheel(d, work, uv) for d in packages.PACKAGES]
        except packages.CheckFailed as exc:
            if "network was disabled" in str(exc):
                pytest.skip("setuptools is not in the offline uv cache")
            raise
        for wheel, directory in zip(wheels, packages.PACKAGES, strict=True):
            assert packages.check_metadata(wheel, directory) == []
        lines = packages.install_and_smoke(wheels, work, uv)
    assert any(line.startswith("import: dclab_client ") for line in lines)
    assert any(line.startswith("dclab-cli --help: usage:") for line in lines)
    assert any(line.startswith("dclab-mcp --help: usage:") for line in lines)
