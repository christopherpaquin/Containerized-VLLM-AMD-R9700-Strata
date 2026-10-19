## How I would diagnose the current implementation first

Before editing the function, I would try to identify whether the problem is:

1. **Crash on unreadable directories**: an unhandled `OSError`/`PermissionError` during traversal or `stat()`.
2. **Symlink loop**: the code is recursing through symbolic-link directories, often by using `os.walk(..., followlinks=True)`, `Path.rglob()`/`Path.glob()`, `os.path.isdir()`, or custom `stat()` logic that follows links.
3. **Memory growth**: the code is likely collecting the entire tree, or all `Path` objects, before filtering, or sorting all files instead of only files larger than 1 GiB.

### 1. Get the exact failure signal

I would run the function against the problematic tree and capture:

- Python traceback:
  - `PermissionError`
  - `OSError: [Errno 5] Input/output error`
  - `RecursionError`
  - `OSError: [Errno 40] Too many levels of symbolic links`
- Filesystem involved: local disk, NFS, FUSE, overlayfs, container mount, etc.
- Python version and OS.
- Whether the failure is deterministic or race-condition-like, such as files/directories disappearing during scan.

If the traceback is missing because the application swallows errors, temporarily add logging before changing behavior:

```python
import traceback
import logging

logging.basicConfig(level=logging.DEBUG)

try:
    result = scan_large_files(root)
except Exception:
    logging.exception("scan_large_files failed on %s", root)
    raise
```

### 2. Inspect the traversal mechanism

I would look for patterns like:

```python
os.walk(root, followlinks=True)
Path(root).rglob("*")
os.path.isdir(child)
os.stat(child)
results.append(child)  # before size filtering
```

Red flags:

| Code pattern | Likely symptom |
|---|---|
| `os.walk(..., followlinks=True)` | symlink loops |
| `Path.rglob("**/*")` without checking documented behavior | may include symlinks depending on Python version, and may require careful error handling |
| `os.path.isdir()` during traversal | follows symlinked directories, can loop |
| `os.stat()` while deciding whether to recurse | follows symlinks unless careful |
| `list(Path.rglob("*"))` | stores entire tree in memory |
| collecting all paths, then filtering by size | stores many irrelevant small files |
| no `try/except OSError` around directory reads or `stat()` | crashes on permission errors, races, I/O errors |

### 3. Reproduce the three failure modes locally

Create a small filesystem-like tree with:

```bash
mkdir -p repro/normal repro/unreadable repro/loop
truncate -s 2G repro/normal/big1.bin
truncate -s 1GiB repro/normal/exactly1.bin
truncate -s 512M repro/normal/small.bin

chmod 000 repro/unreadable

ln -s . repro/loop/self
ln -s .. repro/loop/parent
```

Then run the current implementation on `repro`.

Expected observations:

- If it crashes in the unreadable directory, it is not catching directory access errors.
- If it hangs or blows up in `loop`, it is following symlinked directories.
- If it consumes excessive memory on a synthetic large tree, inspect whether it is storing all files, all `Path` objects, or directory listings.

For large trees, generate many small files and a few large files:

```python
import os
from pathlib import Path

base = Path("/tmp/bigtree")

for i in range(200):
    d = base / f"dir{i}"
    d.mkdir(parents=True, exist_ok=True)

    for j in range(5000):
        p = d / f"small{j}.bin"
        p.touch()
```

Then add a few large sparse files.

### 4. Diagnose memory specifically

Use `tracemalloc` inside the process:

```python
import tracemalloc

tracemalloc.start(25)

result = scan_large_files(root)

snapshot = tracemalloc.take_snapshot()
for stat in snapshot.statistics("lineno")[:10]:
    print(stat)
```

Also monitor RSS externally:

```bash
python app.py &
ps -o pid,rss,cmd -p $!
```

Use `cProfile` or `py-spy` if useful:

```bash
cProfile -o scan.prof app.py
py-spy record -o scan.svg -- python app.py
```

Things to check:

- Is it materializing `Path` objects for every file?
- Is it storing all files, or only matching files?
- Does it sort before filtering?
- Does it use `glob.glob()` recursively, which returns a full list?
- Is the large memory usage actually the output list? If there are millions of files larger than 1 GiB, then any function that returns all sorted files has an unavoidable memory lower bound.

### 5. Diagnose symlink loops with `strace`

On Linux:

```bash
strace -f -e trace=openat,newfstatat,lstat,getdents64 \
  -o scan.strace python app.py
```

Look for repeated access to the same directories or links. Compare behavior with reference tools:

```bash
find -P repro -type f -size +1G
find -L repro -type f -size +1G
```

`-P` does not follow symlinks. `-L` follows them and may reveal loop behavior.

If `find -L` hangs or reports symlink loop errors, that supports the hypothesis that the current Python traversal is following symlinked directories.

### 6. Diagnose unreadable directory crashes with `strace`

For permission problems:

```bash
strace -f -e trace=openat,lstat,newfstatat -o perm.strace python app.py
```

Look for:

```text
openat(...) = -1 EACCES (Permission denied)
```

Also remember that errors can occur not only when opening a directory, but also while iterating through it. That can happen on NFS, FUSE, or during races where a directory disappears mid-scan.

---

## Robust implementation

The implementation below:

- Uses `os.scandir()` instead of `os.listdir()` or `Path.rglob()`.
- Uses an explicit stack instead of recursion.
- Does **not** traverse symbolic-link directories.
- Skips special files such as FIFOs, sockets, devices, etc.
- Catches `OSError` during directory reads, entry classification, and `stat()`.
- Filters during traversal so it only stores matching files.
- Sorts only the matching files.
- Returns paths sorted from largest to smallest.

```python
from __future__ import annotations

import logging
import os
import stat
from typing import Callable, Iterator, List, Tuple

GIB = 1024**3

logger = logging.getLogger(__name__)

ErrorCallback = Callable[[str, OSError], None]


def _default_on_error(path: str, exc: OSError) -> None:
    logger.warning("skipping %s: %s", path, exc)


def iter_large_files(
    root: str | os.PathLike[str],
    *,
    min_size: int = GIB,
    include_symlinked_files: bool = False,
    on_error: ErrorCallback = _default_on_error,
) -> Iterator[Tuple[int, str]]:
    """
    Yield (size_bytes, path) for files larger than min_size.

    Traversal is iterative.

    Symlinked directories are never traversed.

    By default, symlinks are skipped entirely, including symlinks to regular
    files. If include_symlinked_files=True, this function will include
    symbolic links whose final target is a regular file. It still will not
    traverse symlinked directories.

    This avoids symlink loops because directory symlinks are not followed.
    File symlinks can be stat'ed but not traversed.
    """

    if min_size < 0:
        raise ValueError("min_size must be non-negative")

    stack: List[str] = [os.fspath(root)]

    while stack:
        dirpath = stack.pop()

        try:
            iterator = os.scandir(dirpath)
        except OSError as exc:
            on_error(dirpath, exc)
            continue

        try:
            for entry in iterator:
                path = entry.path

                try:
                    # Traverse only real directories.
                    # This deliberately does not follow symlinks that point
                    # to directories, avoiding symlink loops.
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(path)
                        continue

                    # Handle symbolic links separately.
                    if entry.is_symlink():
                        if not include_symlinked_files:
                            continue

                        # Stat the symlink target only to classify it as a
                        # regular file or not. We do not traverse it.
                        st = entry.stat(follow_symlinks=True)
                        if not stat.S_ISREG(st.st_mode):
                            continue

                    else:
                        # Normal regular files only.
                        # Skip FIFOs, sockets, devices, etc.
                        if not entry.is_file(follow_symlinks=False):
                            continue

                        st = entry.stat(follow_symlinks=False)

                    size = st.st_size

                except OSError as exc:
                    # A file may disappear, become unreadable, be a broken
                    # symlink, or the filesystem may return an I/O error.
                    on_error(path, exc)
                    continue

                if size > min_size:
                    yield (size, path)

        except OSError as exc:
            # os.scandir can fail while iterating, especially on network or
            # FUSE filesystems, or if the directory is removed during scan.
            on_error(dirpath, exc)

        finally:
            try:
                iterator.close()
            except OSError:
                pass


def files_larger_than_gib(
    root: str | os.PathLike[str],
    *,
    min_size: int = GIB,
    include_symlinked_files: bool = False,
    on_error: ErrorCallback = _default_on_error,
) -> List[str]:
    """
    Return file paths larger than min_size, sorted largest to smallest.

    Default threshold is 1 GiB using binary GiB: 1024 ** 3.

    The list contains only matching files, not the whole tree. However, if
    there are an enormous number of matching files, the result list itself may
    still be large. See notes below about external sorting.
    """

    matched = list(
        iter_large_files(
            root,
            min_size=min_size,
            include_symlinked_files=include_symlinked_files,
            on_error=on_error,
        )
    )

    # Sort descending by size. Tie-break by path for deterministic behavior.
    matched.sort(key=lambda item: (-item[0], item[1]))

    return [path for _, path in matched]
```

### Notes on behavior

The traversal policy is intentionally conservative:

```python
if entry.is_dir(follow_symlinks=False):
    stack.append(path)
```

This means:

- Regular directories are traversed.
- Symbolic-link directories are ignored.
- Symlink loops are avoided.
- If the original application expected files under symlinked directories, they will not appear. That is a policy decision. If you need to include symlinked directories, you need a cycle-safe traversal using something like a visited set keyed by `(st_dev, st_ino)` or canonical paths.

By default:

```python
include_symlinked_files=False
```

so symlinks are skipped. If you want symlinks to regular files but still do not want symlink loops, use:

```python
include_symlinked_files=True
```

This will:

- include symlinks that point to regular files,
- skip symlinks that point to directories,
- skip dangling symlinks,
- avoid traversing symlinked directories.

### Why this addresses each symptom

#### Unreadable directories

Errors from opening a directory are caught:

```python
try:
    iterator = os.scandir(dirpath)
except OSError as exc:
    on_error(dirpath, exc)
    continue
```

Errors while iterating a directory are caught:

```python
except OSError as exc:
    on_error(dirpath, exc)
```

Errors while inspecting individual entries are caught:

```python
except OSError as exc:
    on_error(path, exc)
    continue
```

So the scan continues rather than crashing.

#### Symlink loops

It uses:

```python
entry.is_dir(follow_symlinks=False)
```

and never appends symlinked directories to the traversal stack.

Therefore a directory like:

```text
loop/
  self -> .
```

will not be followed recursively.

#### Memory usage

It does not collect all files. It only collects files larger than the threshold:

```python
if size > min_size:
    yield (size, path)
```

Sorting is done only on those matches:

```python
matched.sort(...)
```

This fixes the common case where memory was consumed by storing the entire tree or all paths before filtering.

There is still an unavoidable memory cost proportional to the number of files larger than 1 GiB if the function returns all of them as a list. If that number is too large, see the next section.

---

## Testing the robust implementation

A pytest-style test covering unreadable directories, symlink loops, special files, and large files:

```python
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
```

The test assumes non-root execution. On some systems, running as root may ignore `chmod 000`.

---

## If the matching files themselves are too large to sort in memory

The robust implementation reduces memory use by avoiding collection of irrelevant small files. However, the function still returns a list of all matching files. If there are millions or tens of millions of files larger than 1 GiB, the output list may not fit in memory.

In that case, the requirements need to change slightly. Options:

### 1. Return top N files instead of all files

Use a heap:

```python
import heapq


def top_large_files(
    root: str | os.PathLike[str],
    limit: int,
    *,
    min_size: int = GIB,
) -> list[tuple[int, str]]:
    return heapq.nlargest(
        limit,
        iter_large_files(root, min_size=min_size),
        key=lambda item: item[0],
    )
```

This uses memory proportional to `limit`, not to the number of matching files.

### 2. Stream sorted results using external sort

Instead of returning a list, return an iterator that performs chunked external sorting:

- Walk the tree with `iter_large_files()`.
- Append items to an in-memory buffer.
- When the buffer reaches, say, 100,000 matches:
  - sort it descending,
  - write it to a temporary file.
- After the walk:
  - open all temporary sorted chunks,
  - merge them with `heapq.merge()`,
  - yield sorted paths.

This still needs to sort all matching files, but the peak memory can be bounded by the chunk size and number of open temporary files.

A simplified sketch:

```python
import heapq
import pickle
import tempfile


def iter_sorted_large_files_externally(
    root: str | os.PathLike[str],
    *,
    min_size: int = GIB,
    max_buffered: int = 100_000,
    temp_dir: str | None = None,
):
    buffer = []
    temp_files = []

    def flush():
        if not buffer:
            return

        buffer.sort(key=lambda item: (-item[0], item[1]))

        with tempfile.NamedTemporaryFile(
            "wb",
            dir=temp_dir,
            delete=False,
            suffix=".largefiles",
        ) as f:
            path = f.name
            for size, name in buffer:
                pickle.dump((size, name), f, protocol=5)

        temp_files.append(path)
        buffer.clear()

    try:
        for item in iter_large_files(root, min_size=min_size):
            buffer.append(item)
            if len(buffer) >= max_buffered:
                flush()

        if not temp_files:
            buffer.sort(key=lambda item: (-item[0], item[1]))
            for _, path in buffer:
                yield path
            return

        flush()

        def read_file(path):
            with open(path, "rb") as f:
                while True:
                    try:
                        yield pickle.load(f)
                    except EOFError:
                        break

        chunk_iterators = [read_file(path) for path in temp_files]

        for _, path in heapq.merge(
            *chunk_iterators,
            key=lambda item: -item[0],
        ):
            yield path

    finally:
        for path in temp_files:
            try:
                os.remove(path)
            except OSError:
                pass
```

This trades memory for temporary disk space. It is only worth it if the application truly cannot fit all matching results in memory.
