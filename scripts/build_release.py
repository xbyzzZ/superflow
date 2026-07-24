#!/usr/bin/env python3
"""Build a deterministic, installable Superflow release archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
INCLUDED_FILES = ("SKILL.md", "README.md", "README_CN.md", "LICENSE", "VERSION")
INCLUDED_DIRECTORIES = ("agents", "assets", "licenses", "references", "scripts")
EXCLUDED_NAMES = {"__pycache__", ".DS_Store"}
SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


class ReleaseError(RuntimeError):
    """Release input is unsafe, incomplete, or inconsistent."""


def read_version(root: Path = ROOT) -> str:
    version_path = root / "VERSION"
    if version_path.is_symlink() or not version_path.is_file():
        raise ReleaseError("VERSION must be a regular file")
    version = version_path.read_text(encoding="utf-8").strip()
    if SEMVER_RE.fullmatch(version) is None:
        raise ReleaseError("VERSION must contain one Semantic Version")
    return version


def _safe_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for name in INCLUDED_FILES:
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise ReleaseError(f"Required release file is missing or unsafe: {name}")
        files.append(path)
    for name in INCLUDED_DIRECTORIES:
        directory = root / name
        if directory.is_symlink() or not directory.is_dir():
            raise ReleaseError(f"Required release directory is missing or unsafe: {name}")
        for path in directory.rglob("*"):
            if any(part in EXCLUDED_NAMES for part in path.relative_to(root).parts):
                continue
            if path.is_symlink():
                raise ReleaseError(f"Symlinked release path is refused: {path}")
            if path.is_file() and path.suffix not in {".pyc", ".pyo"}:
                files.append(path)
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def _zip_timestamp() -> tuple[int, int, int, int, int, int]:
    raw = os.environ.get("SOURCE_DATE_EPOCH")
    if raw is None:
        return (2020, 1, 1, 0, 0, 0)
    try:
        epoch = int(raw)
    except ValueError as exc:
        raise ReleaseError("SOURCE_DATE_EPOCH must be an integer") from exc
    value = time.gmtime(epoch)
    if value.tm_year < 1980:
        raise ReleaseError("SOURCE_DATE_EPOCH must be representable by ZIP")
    return (
        value.tm_year,
        value.tm_mon,
        value.tm_mday,
        value.tm_hour,
        value.tm_min,
        value.tm_sec - (value.tm_sec % 2),
    )


def _mode(path: Path) -> int:
    return 0o755 if path.parent.name == "scripts" and path.suffix == ".py" else 0o644


def _write_archive(
    archive: Path,
    files: Iterable[Path],
    root: Path,
    timestamp: tuple[int, int, int, int, int, int],
) -> None:
    with zipfile.ZipFile(
        archive,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        strict_timestamps=True,
    ) as bundle:
        for path in files:
            relative = path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(f"superflow/{relative}", date_time=timestamp)
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | _mode(path)) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            info.flag_bits |= 0x800
            bundle.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)


def build_release(output_dir: Path, root: Path = ROOT) -> dict[str, str | int]:
    """Build the release ZIP and SHA-256 sidecar, returning artifact metadata."""
    version = read_version(root)
    files = _safe_files(root)
    output_dir = output_dir.absolute()
    if output_dir.exists() and (output_dir.is_symlink() or not output_dir.is_dir()):
        raise ReleaseError("Release output must be a safe directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f"superflow-v{version}.zip"
    checksum = output_dir / f"superflow-v{version}.zip.sha256"
    if archive.is_symlink() or checksum.is_symlink():
        raise ReleaseError("Symlinked release output is refused")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{archive.name}.", dir=output_dir)
    os.close(descriptor)
    try:
        _write_archive(Path(temporary), files, root, _zip_timestamp())
        os.replace(temporary, archive)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    return {
        "version": version,
        "archive": str(archive),
        "checksum": str(checksum),
        "sha256": digest,
        "files": len(files),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = {"ok": True, **build_release(args.output_dir)}
        code = 0
    except (OSError, ReleaseError, zipfile.BadZipFile) as exc:
        result = {"ok": False, "error": str(exc)}
        code = 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
