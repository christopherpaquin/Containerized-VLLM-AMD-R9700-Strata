import os
import stat
from pathlib import Path

def test_unreadable_directory_is_skipped(tmp_path):
    readable = tmp_path / "readable"
    unreadable = tmp_path / "unreadable"

    readable.mkdir()
    unreadable.mkdir()

    big = readable / "big"
    big.write_bytes(b"\0" * (2 * 1024**3))

    unreadable.chmod(0o000)

    try:
        found = find_large_files(tmp_path)
    finally:
        unreadable.chmod(0o755)

    assert str(big) in [str(p) for p in found]
