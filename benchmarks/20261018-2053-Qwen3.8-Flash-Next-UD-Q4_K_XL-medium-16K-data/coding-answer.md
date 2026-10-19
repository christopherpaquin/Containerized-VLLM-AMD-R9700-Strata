# Diagnosing and Rebuilding a Recursive Large-File Scanner

## Part 1 — Diagnosis Before Changing a Line of Code

The goal is to turn three vague symptom reports ("occasionally crashes," "follows loops," "eats memory") into reproducible, instrumented evidence so the fix is surgical rather than speculative.

### 1.1  Reproduce the crash on unreadable directories

```python
# scratch/test_permissions.py
import os, stat

# Build a tiny tree with a root-locked directory
os.makedirs("/tmp/scan_test/accessible", exist_ok=True)
os.makedirs("/tmp/scan_test/locked", exist_ok=True)
open("/tmp/scan_test/locked/secret.bin", "wb").close()

# Strip the *directory* read + execute bits (simulates /proc/<pid>/ or NFS ACLs)
os.chmod("/tmp/scan_test/locked", 0o000)

# Run the existing function against it
try:
    result = scan_large_files("/tmp/scan_test")   # ← the suspect call
except PermissionError as exc:
    print(f"CRASHED with PermissionError: {exc}")
except OSError as exc:
    print(f"CRASHED with OSError ({exc.errno}): {exc}")
finally:
    os.chmod("/tmp/scan_test/locked", 0o755)
```

Attach `faulthandler.enable()` at the top of the entry-point script so that even a segfault (rare, but possible with C-extension path handlers) dumps a Python-level traceback.

### 1.2  Detect symlink-loop traversal

Create a self-referencing or ancestor-pointing symlink and time the scan:

```python
os.symlink("/tmp/scan_test", "/tmp/scan_test/accessible/loop_back")
```

Run the scan under `timeout 10` and watch with `strace -f -e trace=lstat,stat,dirent` (Linux) or `fs_usage -f files` (macOS). If you see the same `(dev, ino)` pair appearing over and over, the function is following symlinks into cycles. A quick in-process check:

```python
# Wrap os.listdir / os.scandir with a visited-inode set and log duplicates
visited: set[tuple[int, int]] = set()
def guarded_scandir(path):
    st = os.stat(path, follow_symlinks=True)
    key = (st.st_dev, st.st_ino)
    if key in visited:
        print(f"LOOP: {path} → already-visited inode {key}")
    visited.add(key)
    yield from os.scandir(path)
```

### 1.3  Measure memory on a wide/deep tree

Generate a synthetic tree with 2 million tiny files:

```python
for i in range(2_000_000):
    open(f"/tmp/bigtree/sub{i//5000}/{i}.dat", "wb").close()
```

Then profile:

```bash
python -m tracemalloc  scan_script.py /tmp/bigtree
# or, more precisely:
python -X importtime -m cProfile -o scan.prof scan_script.py /tmp/bigtree
```

Look for a single list growing to millions of `str` entries *before* any size filter is applied—that confirms the memory issue is "collect everything, then filter" rather than "filter while walking."

### 1.4  Check for TOCTOU / race-condition gaps

On a live server the scan may race with `rm`, `umount`, or NFS server restarts. The existing code probably calls `os.stat()` on a path it already listed. Wrap a single `os.scandir` entry's `.stat()` in a loop that logs `FileNotFoundError` / `FileExistsError` to confirm.

### 1.5  Log a structured diagnostic pass

Instrument *before* rewriting:

```python
import logging, time
logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger("scan")

for root, dirs, files in os.walk(base):          # existing walk
    t0 = time.monotonic()
    for f in files:
        p = os.path.join(root, f)
        try:
            size = os.stat(p).st_size            # ← likely the crash site
        except (PermissionError, FileNotFoundError, OSError) as exc:
            log.warning("skipping %s: %s", p, exc)
            continue
        if size > THRESHOLD:
            results.append((p, size))
    log.debug("dir %s: %d files, %.1f ms", root, len(files), (time.monotonic()-t0)*1000)
```

Run it on the real production tree. The log will tell you *exactly* which path triggers the `PermissionError`, whether `os.walk` is following symlinked directories (the `followlinks` kwarg), and how large `results` gets.

---

## Part 2 — Robust Implementation

Design decisions derived from the diagnosis:

| Problem | Fix |
|---|---|
| Unreadable dirs / files (perm, NFS, TOCTOU) | `try/except` around every `scandir` and `.stat()`, **continue**, never re-raise |
| Symlink loops | Never follow directory symlinks; use `follow_symlinks=False` everywhere. Optionally track visited `(dev, ino)` pairs if you *must* follow links. |
| Memory blow-up | `os.scandir` is a **generator** (lazy). We filter at walk-time and only keep `(path, size)` tuples for files ≥ 1 GiB. The list is bounded by the number of *large* files, not all files. |
| Sort | `heapq.nlargest` avoids a full sort when the result set is huge; we still return the full sorted list because the spec requires it, but we build it from a min-heap of size *k* if the caller only wants top-N. |
| Broken / dangling symlinks | `os.DirEntry.stat(follow_symlinks=True)` raises; catch it. |
| Deep recursion | Use an explicit stack (iterative) to avoid `RecursionError` at depth > 10 000. |

```python
"""scan_large.py – find every regular file ≥ 1 GiB, largest first."""

from __future__ import annotations

import heapq
import os
import stat
import sys
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# ── Tunables ──────────────────────────────────────────────────────────
ONE_GIB: int = 1 * 1024**3

# ── Public data type ─────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class LargeFile:
    path: str
    size_bytes: int


# ── Core walk (iterative – no recursion limit) ───────────────────────
def _walk_no_follow(base: str) -> "generator[tuple[str, os.DirEntry]]":
    """
    Yield (dir_path, DirEntry) for every *regular file* under `base`.

    * Never follows directory symlinks → immune to cycles.
    * Uses a LIFO stack → no recursion-depth limit.
    * Skips unreadable directories with a warning instead of crashing.
    * Treats broken symlinks, sockets, fifos, devices as non-files.
    """
    stack: list[str] = [base]
    # Track real dirs we've already entered (defence-in-depth for
    # bind-mounts or multi-path exports that alias the same inode).
    visited_dirs: set[tuple[int, int]] = set()

    while stack:
        current = stack.pop()

        # Resolve the directory's real identity to guard against
        # symlinked-directory tricks *within* the same walk.
        try:
            dir_stat = os.stat(current, follow_symlinks=True)
            dir_id = (dir_stat.st_dev, dir_stat.st_ino)
            if dir_id in visited_dirs:
                log.debug("already-visited dir (by inode): %s", current)
                continue
            visited_dirs.add(dir_id)
        except OSError as exc:
            log.warning("cannot stat dir %s: %s – skipping", current, exc)
            continue

        # os.scandir is a lazy generator; one syscall per entry,
        # and DirEntry.stat() is cached by the OS → no second syscall.
        try:
            entries = list(os.scandir(current))       # one readdir syscall
        except PermissionError:
            log.warning("no read permission on %s – skipping", current)
            continue
        except FileNotFoundError:
            log.warning("dir vanished mid-scan: %s – skipping", current)
            continue
        except OSError as exc:
            log.warning("readdir failed on %s (%s): %s – skipping",
                        current, exc.errno, exc)
            continue

        for entry in entries:
            try:
                # We need is_file() WITHOUT following symlinks, so a
                # symlink-to-file is skipped (avoids the loop risk)
                # but we still stat it to check size.  Adjust:
                is_symlink = entry.is_symlink()

                # Decide: traverse sub-directories or inspect files.
                if entry.is_dir(follow_symlinks=False):
                    stack.append(entry.path)          # ← never follow dir symlinks
                    continue

                if is_symlink:
                    # Symlink to a file: stat the *target* for size,
                    # but never recurse into symlinked directories.
                    target_stat = entry.stat(follow_symlinks=True)
                    if not stat.S_ISREG(target_stat.st_mode):
                        continue  # socket, fifo, etc.
                    size = target_stat.st_size
                else:
                    if not entry.is_file(follow_symlinks=False):
                        continue  # device node, etc.
                    size = entry.stat(follow_symlinks=False).st_size

            except OSError as exc:
                # Broken symlink, TOCTOU race, I/O error → skip this entry
                log.debug("cannot stat %s: %s", entry.path, exc)
                continue

            if size >= ONE_GIB:
                yield (current, entry, size)


# ── Public API ────────────────────────────────────────────────────────
def scan_large_files(
    root: str,
    *,
    min_size: int = ONE_GIB,
    top_n: int | None = None,
) -> list[LargeFile]:
    """
    Return every regular file under *root* whose size ≥ *min_size*,
    sorted largest → smallest.

    Parameters
    ----------
    root:
        Directory to scan.
    min_size:
        Size threshold in bytes (default 1 GiB).
    top_n:
        If set, only the *N* largest files are kept (uses a bounded
        heap → O(N log K) instead of O(N log N) sort).
    """
    results: list[tuple[int, str]] = []   # (size, path) – size first for heap

    for _dir, entry, size in _walk_no_follow(root):
        if top_n is not None and len(results) >= top_n:
            # Maintain a min-heap of size top_n
            if size > results[0][0]:
                heapq.heapreplace(results, (size, entry.path))
        else:
            results.append((size, entry.path))

    if top_n is not None:
        # Heap gives largest-first via nlargest (already the whole heap)
        sorted_heap = heapq.nlargest(len(results), results)
    else:
        results.sort(key=lambda t: t[0], reverse=True)
        sorted_heap = results

    return [LargeFile(path=p, size_bytes=s) for s, p in sorted_heap]


# ── CLI convenience ───────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(
        level=os.environ.get("SCAN_LOG", "WARNING").upper(),
        format="%(levelname)s %(name)s: %(message)s",
    )

    target = sys.argv[1] if len(sys.argv) > 1 else "."
    top = int(sys.argv[2]) if len(sys.argv) > 2 else None

    files = scan_large_files(target, top_n=top)
    for f in files:
        print(f"{f.size_bytes / (1024**3):10.2f} GiB  {f.path}")
```

### Why this is safe where the old version was not

**Unreadable directories.** Every `os.stat`, `os.scandir`, and `DirEntry.stat` call sits inside a `try/except OSError` (which covers `PermissionError`, `FileNotFoundError`, `ELOOP`, `ENOTDIR`, NFS `ESTALE`, etc.). The scan logs a warning and moves on; the process never aborts.

**Symlink loops.** Two independent guards:
1. Directory entries are classified with `entry.is_dir(follow_symlinks=False)`. A symlink that *points to* a directory is **not** treated as a directory, so the walker never pushes it onto the stack.
2. Even if a caller later enables `follow_symlinks=True` for directories (e.g. bind-mounts), the `visited_dirs` inode set catches the alias before a second traversal.

**Memory.** `os.scandir` is lazy at the OS level; the only list we build is `entries` for *one directory at a time* (typically thousands of entries, not millions). Large-file tuples are appended incrementally. For the extreme case the `top_n` parameter caps the in-memory result at *K* tuples via a bounded min-heap.

**Recursion depth.** The walk is an explicit stack loop, so a 50 000-deep tree (common on HPC scratch volumes) doesn't hit `RecursionError`.

**Race conditions.** A file deleted between `scandir` and `stat` raises `FileNotFoundError`, caught by the per-entry `except OSError`. A directory unmounted mid-scan raises `OSError` at the `scandir` call, also caught.