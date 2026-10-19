import os
from pathlib import Path

base = Path("/tmp/bigtree")

for i in range(200):
    d = base / f"dir{i}"
    d.mkdir(parents=True, exist_ok=True)

    for j in range(5000):
        p = d / f"small{j}.bin"
        p.touch()
