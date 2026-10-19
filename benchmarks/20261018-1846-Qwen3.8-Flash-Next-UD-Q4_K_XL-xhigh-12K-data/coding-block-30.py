from pathlib import Path

def test_threshold(tmp_path):
    small = tmp_path / "small"
    big = tmp_path / "big"

    small.write_bytes(b"\0" * 1024)
    big.write_bytes(b"\0" * (2 * 1024**3))

    found = find_large_files(tmp_path)

    assert str(big) in [str(p) for p in found]
    assert str(small) not in [str(p) for p in found]
