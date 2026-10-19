from pathlib import Path

def test_sorted_descending(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    c = tmp_path / "c"

    a.write_bytes(b"\0" * (2 * 1024**3))
    b.write_bytes(b"\0" * (5 * 1024**3))
    c.write_bytes(b"\0" * (1 * 1024**3 + 1))

    found = [str(p) for p in find_large_files(tmp_path)]

    assert found == [str(b), str(a), str(c)]
