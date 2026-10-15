"""Build the publishable packages as wheels and prove they install and run (P8.6-A).

    python -m scripts.check_packages_build

For ``packages/dclab_client`` and ``packages/dclab_mcp``: copy the source to a temp
dir (so setuptools' ``build/`` never lands in the repo), build a wheel with
``uv build --offline``, check the wheel metadata is honest (name, version, summary,
README, requires-python, classifiers, entry points; no license claim while the license
is undecided, docs/adr/0011-open-source-license.md; no tests packaged), install both
wheels into a fresh venv in the temp dir with ``uv pip install --offline`` and run
``import`` plus each console script's ``--help``.

Offline: the build backend (setuptools) and the runtime dependencies must already be
in the local uv cache. If they are not, the step fails with uv's own message; this
script never fetches anything itself.
"""

from __future__ import annotations

import email
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {
    # directory: (distribution, import name, console scripts)
    "dclab_client": ("dclab-client", "dclab_client", ("dclab-cli",)),
    "dclab_mcp": ("dclab-mcp", "dclab_mcp", ("dclab-mcp",)),
}
LICENSE_FIELDS = ("License", "License-Expression", "License-File")


class CheckFailed(RuntimeError):
    pass


def _run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True, check=False, **kwargs)
    if result.returncode != 0:
        raise CheckFailed(f"{' '.join(command)} exited {result.returncode}:\n{result.stderr or result.stdout}")
    return result


def build_wheel(directory: str, work: Path, uv: str) -> Path:
    source = work / "src" / directory
    shutil.copytree(
        REPO_ROOT / "packages" / directory, source,
        ignore=shutil.ignore_patterns("build", "dist", "*.egg-info", "__pycache__", ".pytest_cache"),
    )
    _run([uv, "build", "--offline", "--wheel", "--out-dir", str(work / "dist"), str(source)])
    distribution = PACKAGES[directory][0].replace("-", "_")
    wheels = sorted((work / "dist").glob(f"{distribution}-*.whl"))
    if len(wheels) != 1:
        raise CheckFailed(f"expected one {distribution} wheel, found {[w.name for w in wheels]}")
    return wheels[0]


def check_metadata(wheel: Path, directory: str) -> list[str]:
    """Problems with the wheel's metadata and contents (empty when honest)."""
    distribution, module, scripts = PACKAGES[directory]
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        meta_name = next(n for n in names if n.endswith(".dist-info/METADATA"))
        meta = email.message_from_string(archive.read(meta_name).decode())
        entry_points = archive.read(meta_name.replace("METADATA", "entry_points.txt")).decode()
    problems = []
    if meta["Name"] != distribution:
        problems.append(f"Name is {meta['Name']!r}")
    for field in ("Version", "Summary", "Requires-Python"):
        if not meta[field]:
            problems.append(f"missing {field}")
    if not (meta.get_payload() or "").strip() or meta["Description-Content-Type"] != "text/markdown":
        problems.append("README is not the long description")
    classifiers = meta.get_all("Classifier") or []
    if "Private :: Do Not Upload" not in classifiers:
        problems.append("missing the 'Private :: Do Not Upload' guard while the license is undecided")
    problems += [f"claims a license: {f}" for f in LICENSE_FIELDS if meta[f]]
    problems += [f"claims a license: {c}" for c in classifiers if c.startswith("License ::")]
    for requirement in meta.get_all("Requires-Dist") or []:
        if "==" in requirement:
            problems.append(f"exact pin in a published dependency: {requirement}")
    problems += [f"missing console script {s}" for s in scripts if f"{s} = " not in entry_points]
    problems += [f"packages a test file: {n}" for n in names if "/tests/" in n or n.startswith("tests/")]
    if not any(n.startswith(f"{module}/") for n in names):
        problems.append(f"{module} is not in the wheel")
    return problems


def _requirements_met_here(wheels: list[Path]) -> list[str]:
    """Each wheel's Requires-Dist checked against the distributions installed in the
    running interpreter's environment (the one borrowed for the smoke run)."""
    from importlib.metadata import PackageNotFoundError, version

    from packaging.requirements import Requirement

    local = {PACKAGES[d][0] for d in PACKAGES}
    problems = []
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            meta_name = next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))
            meta = email.message_from_string(archive.read(meta_name).decode())
        for raw in meta.get_all("Requires-Dist") or []:
            requirement = Requirement(raw)
            if requirement.name in local or (requirement.marker and not requirement.marker.evaluate()):
                continue
            try:
                installed = version(requirement.name)
            except PackageNotFoundError:
                problems.append(f"{requirement} is not installed in {sys.prefix}")
                continue
            if not requirement.specifier.contains(installed, prereleases=True):
                problems.append(f"{requirement} does not admit the installed {requirement.name} {installed}")
    return problems


def install_and_smoke(wheels: list[Path], work: Path, uv: str) -> list[str]:
    venv = work / "venv"
    _run([uv, "venv", "--offline", "--python", sys.executable, str(venv)])
    python = venv / ("Scripts" if sys.platform == "win32" else "bin") / "python"
    lines = []
    try:
        _run([uv, "pip", "install", "--offline", "--python", str(python), *map(str, wheels)])
        lines.append("install: wheels + dependencies resolved offline from the uv cache")
    except CheckFailed as exc:
        if "network was disabled" not in str(exc):
            raise
        # The cache cannot resolve every dependency without a network. Install the wheels
        # alone and borrow this interpreter's environment for the third-party deps (after
        # the fresh venv's own site-packages, so dclab_* come from the wheels), and say so.
        _run([uv, "pip", "install", "--offline", "--no-deps", "--python", str(python), *map(str, wheels)])
        purelib = _run([str(python), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"]).stdout
        borrowed = sysconfig.get_paths()["purelib"]
        (Path(purelib.strip()) / "zz_borrowed_deps.pth").write_text(borrowed + "\n", encoding="utf-8")
        problems = _requirements_met_here(wheels)
        if problems:
            raise CheckFailed("declared dependencies vs the borrowed environment:\n  " + "\n  ".join(problems))
        lines.append(
            "install: NOT resolved from an index (uv cache incomplete offline); wheels installed --no-deps, "
            f"declared dependency ranges checked against and borrowed from {sys.prefix}"
        )
    code = (
        "import dclab_client, dclab_mcp; print(dclab_client.__version__, dclab_mcp.__version__, "
        "dclab_client.__file__, dclab_mcp.__file__)"
    )
    out = _run([str(python), "-I", "-c", code], cwd=work).stdout.strip()
    if any(str(REPO_ROOT) in part for part in out.split()[2:]):
        raise CheckFailed(f"imported from the repository, not the wheel: {out}")
    lines.append("import: dclab_client " + " / dclab_mcp ".join(out.split()[:2]) + " (from the fresh venv)")
    for _, _, scripts in PACKAGES.values():
        for script in scripts:
            out = _run([str(python.parent / script), "--help"], cwd=work, timeout=60).stdout
            lines.append(f"{script} --help: {out.splitlines()[0] if out else '(no output)'}")
    return lines


def main() -> int:
    uv = shutil.which("uv")
    if uv is None:
        print("NOT RUN: uv is not on PATH (needed for an offline build and install)")
        return 2
    with tempfile.TemporaryDirectory(prefix="dclab-pkg-check-") as tmp:
        work = Path(tmp)
        try:
            wheels = []
            for directory in PACKAGES:
                wheel = build_wheel(directory, work, uv)
                problems = check_metadata(wheel, directory)
                print(f"built {wheel.name}: {'metadata OK' if not problems else 'metadata problems'}")
                for problem in problems:
                    print(f"  - {problem}")
                if problems:
                    return 1
                wheels.append(wheel)
            for line in install_and_smoke(wheels, work, uv):
                print(line)
        except CheckFailed as exc:
            print(f"FAILED: {exc}")
            return 1
    print("packages build: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
