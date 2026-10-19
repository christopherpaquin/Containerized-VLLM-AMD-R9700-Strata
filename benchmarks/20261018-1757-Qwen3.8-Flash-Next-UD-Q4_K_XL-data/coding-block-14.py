import os
from pathlib import Path

import pytest

from large_files import GIB, files_larger_than_gib


@pytest.fixture
def test_tree(tmp_path: Path):
    normal = tmp_path / "normal"
    normal.mkdir()

    big = normal / "big.bin"
    big.touch()
    os.truncate(big, GIB + 1)

    exactly = normal / "exactly-1-gib.bin"
    exactly.touch()
    os.truncate(exactly, GIB)

    smaller = normal / "small.bin"
    smaller.touch()
    os.truncate(smaller, GIB - 1)

    unreadable = tmp_path / "unreadable"
    unreadable.mkdir()
    hidden_big = unreadable / "hidden-big.bin"
    hidden_big.touch()
    os.truncate(hidden_big, 2 * GIB)

    os.symlink(tmp_path, tmp_path / "self-loop")

    os.symlink(big, tmp_path / "big-symlink.bin")

    os.symlink(normal, tmp_path / "dir-symlink")

    os.symlink(tmp_path / "missing", tmp_path / "dangling-symlink")

    unreadable.chmod(0o000)

    yield tmp_path

    unreadable.chmod(0o700)


def test_files_larger_than_gib(tmp_path):
    files = files_larger_than_gib(tmp_path)

    assert len(files) == 1
    assert files[0].endswith("big.bin")


def test_include_symlinked_files(tmp_path):
    files = files_larger_than_gib(
        tmp_path,
        include_symlinked_files=True,
    )

    assert any(p.endswith("big-symlink.bin") for p in files)
