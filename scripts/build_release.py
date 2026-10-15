"""Build and inspect local distribution files; never publish anything."""
from __future__ import annotations

import ast
import contextlib
import hashlib
import io
import os
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    if Path.cwd() != root:
        raise SystemExit("Run this script from the repository root.")
    from setuptools.build_meta import build_sdist, build_wheel

    version_tree = ast.parse((root / "src/whynotup/__init__.py").read_text(encoding="utf-8"))
    version = next(node.value.value for node in version_tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets))
    destination = root / "dist"
    destination.mkdir(exist_ok=True)
    listed = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root).decode().split("\0")
    selected = sorted(set(listed) - {""})
    # An isolated source copy prevents setuptools from shipping stale files
    # from an earlier build/lib or egg-info directory in the checkout.
    with tempfile.TemporaryDirectory(prefix="whynotup-release-") as temporary:
        clean = Path(temporary) / "whynotup"
        clean.mkdir()
        for name in selected:
            path = root / name
            if path.is_file():
                if not path.resolve().is_relative_to(root):
                    raise SystemExit("Refusing a source symlink outside the repository.")
                target = clean / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
        try:
            os.chdir(clean)
            with contextlib.redirect_stdout(io.StringIO()):
                wheel = destination / build_wheel(str(destination))
                source = destination / build_sdist(str(destination))
        finally:
            os.chdir(root)
    with zipfile.ZipFile(wheel) as archive:
        for expected in ("whynotup/demo/compose.yaml", "whynotup/demo/demo.env", "whynotup/action.py"):
            if expected not in archive.namelist():
                raise SystemExit(f"Wheel is missing {expected}")
    with tarfile.open(source) as archive:
        names = archive.getnames()
        for expected in ("README.es.md", "examples/broken/.env", "examples/fixed/.env", "action.yml",
                         "tests/integration/test_docker_runtime.py", ".github/workflows/docker-integration.yml",
                         "scripts/build_release.py"):
            if not any(name.endswith("/" + expected) for name in names):
                raise SystemExit(f"Source distribution is missing {expected}")
    bundle = destination / f"whynotup-{version}-source.zip"
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in selected:
            path = root / name
            if path.is_file():
                archive.write(path, "whynotup/" + name)
    assets = (wheel, source, bundle)
    checksums = destination / "SHA256SUMS"
    checksums.write_text("".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in assets), encoding="utf-8")
    for path in (*assets, checksums):
        print(path.relative_to(root))


if __name__ == "__main__":
    main()
