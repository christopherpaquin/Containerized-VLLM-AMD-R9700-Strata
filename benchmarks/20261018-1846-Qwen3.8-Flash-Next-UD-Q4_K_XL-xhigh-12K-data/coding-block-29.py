from pathlib import Path

def test_symlink_loop_terminates(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "a" / "b"

    a.mkdir()
    b.mkdir()

    loop = b / "loop_to_a"
    loop.symlink_to(a)

    found = find_large_files(tmp_path)

    assert isinstance(found, list)
