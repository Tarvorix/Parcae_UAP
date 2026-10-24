"""Package repository source plus explicitly generated public fixtures."""

import argparse
import hashlib
import subprocess
import zipfile
from pathlib import Path


def package(output: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    names = (
        subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root,
        )
        .decode()
        .split("\0")
    )
    names = [name for name in names if name]
    names += ["artifacts/fixtures/sky.mkv", "artifacts/fixtures/sky.truth.json"]
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(set(names)):
            path = root / name
            if path.is_symlink() or name.endswith((".sqlite", ".env")):
                raise ValueError("Unexpected source package member")
            archive.write(path, "uap-observer/" + name)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip():
            raise ValueError("ZIP CRC failure")
    with output.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    print(f"{output}: {output.stat().st_size} bytes; sha256={digest}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    package(parser.parse_args().output)
