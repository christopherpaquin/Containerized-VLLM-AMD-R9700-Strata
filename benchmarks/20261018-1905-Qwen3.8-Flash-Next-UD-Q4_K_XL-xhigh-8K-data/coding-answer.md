## How I would diagnose it before changing behavior

I would first characterize failures and resource growth without fixing anything.

1. **Reproduce each failure class separately**
   - Create a controlled test tree containing:
     - regular files below/above 1 GiB
     - unreadable directories (`chmod 000`)
     - broken symlinks
     - a symlink directory pointing to an ancestor directory
     - very many entries
   - Run the current function against each case with traceback logging enabled.
   - If it crashes, inspect the traceback to identify the exact syscall:
     - `os.scandir` failure?
     - `os.stat` failure?
     - `os.walk` iteration failure?
     - `Path.rglob`/`glob` recursion?

2. **Identify symlink-loop behavior**
   - Check whether the implementation uses:
     - `os.walk(..., followlinks=True)`
     - `pathlib.Path.rglob`
     - recursive manual `stat`/`scandir` code that follows directory symlinks
   - Compare behavior with `find -P`, `find -L`, and `du` to understand expected traversal.
   - If it follows directory symlinks, instrument the traversal to log `(st_dev, st_ino)` for directories and look for repeats.

3. **Identify memory usage source**
   - Use `tracemalloc`:
     ```python
     import tracemalloc

     tracemalloc.start()
     result = scan(path)
     snapshot = tracemalloc.take_snapshot()
     for stat in snapshot.statistics("lineno")[:20]:
         print(stat)
     ```
   - Check whether the implementation stores:
     - every path before filtering
     - every directory entry before filtering
     - both path and `DirEntry` objects
     - full `os.walk` result lists
   - Monitor process RSS while scanning a huge tree.
   - Determine whether memory is proportional to:
     - total files scanned, or
     - only files above threshold, or
     - output list size.

4. **Check current API expectations**
   - Does it return a `list[str]`, `list[Path]`, generator, etc.?
   - Does it expect `> 1 GiB` or `>= 1 GiB`?
   - Should symlinks themselves count, or only their targets?
   - Should the scanner fail when the root is unreadable, or return empty/skip errors?

---

## Robust implementation

This implementation:

- uses `os.scandir`, not recursion, to avoid recursion-depth issues
- does **not** follow directory symlinks by default, avoiding symlink loops
- catches `OSError` around directory access and file stat calls, so unreadable paths are skipped
- avoids storing the whole directory tree, storing only files above the threshold
- sorts the matching files by descending size
- keeps memory proportional to the number of matching files, not the number of scanned files

If the number of matching files itself is too large for memory, a fully sorted `list[str]` is impossible without disk-backed sorting; I include a streaming variant idea at the end.

```python
from __future__ import annotations

import os
from operator import itemgetter
from typing import Callable, Optional

GIBIBYTE = 1024 * 1024 * 1024

ErrorHandler = Callable[[OSError, Optional[str]], None]


def files_larger_than_1gib(
    root: str | os.PathLike[str],
    *,
    min_size: int = GIBIBYTE,
    follow_dir_symlinks: bool = False,
    include_symlinked_files: bool = False,
    on_error: Optional[ErrorHandler] = None,
) -> list[str]:
    """
    Return paths of files under root whose size is strictly greater than
    min_size, sorted from largest to smallest.

    Default behavior:
    - skips unreadable directories and unreadable files
    - does not recurse into directory symlinks, avoiding symlink loops
    - counts regular files only
    - does not follow symlinks to files when determining size

    If include_symlinked_files=True, symlinks whose targets are regular files
    may be included using the target file's size. Directory symlink loops are
    still avoided unless follow_dir_symlinks=True.

    If follow_dir_symlinks=True, directory symlinks are followed safely using
    a visited set of (st_dev, st_ino) directory identities. This can consume
    memory proportional to the number of directories visited.
    """

    root_path = os.fspath(root)

    results: list[tuple[int, str]] = []
    stack: list[str] = [root_path]

    visited_dirs: Optional[set[tuple[int, int]]] = (
        set() if follow_dir_symlinks else None
    )

    def report_error(exc: OSError, path: Optional[str]) -> None:
        if on_error is not None:
            try:
                on_error(exc, path)
            except Exception:
                # Never let an error callback break the scan.
                pass

    while stack:
        directory = stack.pop()

        try:
            entries = os.scandir(directory)
        except OSError as exc:
            report_error(exc, directory)
            continue

        with entries:
            for entry in entries:
                try:
                    # When follow_dir_symlinks is False, a symlink to a
                    # directory is not considered a directory and is skipped.
                    is_dir = entry.is_dir(follow_symlinks=follow_dir_symlinks)
                except OSError as exc:
                    report_error(exc, entry.path)
                    continue

                if is_dir:
                    if visited_dirs is not None:
                        try:
                            st = entry.stat(follow_symlinks=True)
                        except OSError as exc:
                            report_error(exc, entry.path)
                            continue

                        key = (st.st_dev, st.st_ino)
                        if key in visited_dirs:
                            continue

                        visited_dirs.add(key)

                    stack.append(entry.path)
                    continue

                try:
                    # By default, use lstat semantics: only regular files.
                    # A symlink to a large file will not count unless
                    # include_symlinked_files=True.
                    is_file = entry.is_file(follow_symlinks=include_symlinked_files)
                except OSError as exc:
                    report_error(exc, entry.path)
                    continue

                if not is_file:
                    continue

                try:
                    st = entry.stat(follow_symlinks=include_symlinked_files)
                except OSError as exc:
                    report_error(exc, entry.path)
                    continue

                if st.st_size > min_size:
                    results.append((st.st_size, entry.path))

    results.sort(key=itemgetter(0), reverse=True)

    # Convert (size, path) tuples into paths without creating a second list.
    # This keeps peak memory closer to the final output size.
    for i, (_, path) in enumerate(results):
        results[i] = path  # type: ignore[assignment]

    return results  # type: ignore[return-value]
```

### Example usage

```python
import sys

def log_error(exc: OSError, path: str | None) -> None:
    print(f"skipping {path!r}: {exc}", file=sys.stderr)

paths = files_larger_than_1gib("/data", on_error=log_error)

for path in paths:
    print(path)
```

---

## If the output itself is too large

A function that returns `list[str]` must hold every matching path in memory. If the tree contains millions or billions of files larger than 1 GiB, returning a fully sorted list cannot be made memory-safe without changing the API.

In that case I would expose a streaming API:

```python
def iter_files_larger_than_1gib(...) -> Iterator[str]:
    ...
```

and sort it using an external merge sort with temporary chunk files. The scanner would yield `(size, path)` pairs, sorted chunks would be written to disk, then `heapq.merge` would produce a globally sorted stream. The caller could then either consume the iterator lazily or materialize a list only if that fits in memory.
