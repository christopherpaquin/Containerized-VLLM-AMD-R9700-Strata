## Diagnosis before changing the code

I would not start by “adding try/except everywhere” or by replacing the recursion blindly. I would first characterize the existing behavior.

### 1. Inspect the current implementation

I would look for the common causes of the three symptoms:

#### Crashes on unreadable directories

Look for calls such as:

```python
os.listdir(path)
os.walk(path)          # without onerror=...
os.path.isdir(path)
os.path.getsize(path)
os.stat(path)
```

Typical failure:

```python
os.listdir(unreadable_dir)
```

raises `PermissionError`, `NotADirectoryError`, `FileNotFoundError`, `OSError`, etc. If there is no `onerror`, no `try/except`, or no logging context, the whole scan dies.

#### Symlink loops

Look for:

```python
os.walk(..., followlinks=True)
```

or:

```python
os.path.isdir(entry)
```

because `os.path.isdir()` follows symlinks. A recursive directory scanner that uses `os.path.isdir()` or `followlinks=True` can loop forever through:

```text
dir/link_to_parent -> ..
```

A good scanner should use `os.lstat()`/`stat.S_ISLNK()` or `os.DirEntry.stat(follow_symlinks=False)` for traversal unless it explicitly tracks visited directories.

#### Excessive memory

Look for:

```python
all_files = []
...
all_files.append(path)
...
all_files.sort(...)
```

or:

```python
os.walk(...)
```

combined with accumulating all paths before filtering or sorting.

Also check whether the scanner stores:

- every path encountered, even if smaller than 1 GiB;
- every file name in memory before processing;
- full directory lists for every directory;
- `(path, size)` tuples for every file before filtering;
- deep recursive Python call frames.

A recursive scanner can also hit `RecursionError` on very deep trees.

---

### 2. Reproduce the failure modes in isolation

I would build small controlled trees.

#### Unreadable directory

```bash
mkdir -p tree/unreadable
touch tree/small
dd if=/dev/zero of=tree/big bs=1M count=1024
chmod 000 tree/unreadable
```

Run the scanner and observe the traceback.

Expected crash examples:

```text
PermissionError: [Errno 13] Permission denied: 'tree/unreadable'
```

or:

```text
NotADirectoryError: [Errno 20] Not a directory: ...
```

#### Symlink loop

```bash
mkdir -p tree/sub
ln -s .. tree/sub/loop_to_parent
ln -s self tree/self
```

If the scan hangs, CPU grows but I/O is cyclic, or the process dies after reaching a recursion limit, that strongly suggests symlink-following recursion.

#### Deep/wide tree

Create a large synthetic tree:

```bash
python -m mktemp -d
# or script that creates many directories and files
```

Then run with monitoring.

---

### 3. Add observability without changing semantics

Before fixing, I would wrap or instrument the current code lightly to capture:

- the directory being scanned when an exception occurs;
- the entry being processed;
- symlink targets;
- approximate memory usage;
- whether the scan is progressing or looping.

Example instrumentation around the current scanner:

```python
import logging
import traceback

logger = logging.getLogger("large_files")

def scan(root):
    logger.info("Scanning %r", root)
    try:
        ...  # original scan logic
    except Exception:
        logger.exception("Scan failed while scanning %r", root)
        raise
```

For symlink diagnosis:

```python
import os
import logging

logger = logging.getLogger("large_files")

def inspect_entry(entry):
    logger.debug("Entry: %r", entry.path)
    if entry.is_symlink():
        logger.debug("Symlink target: %r", os.readlink(entry.path))
```

For directory access errors:

```python
try:
    os.scandir(path)
except OSError as exc:
    logger.warning("Cannot open directory %r: %s", path, exc)
```

---

### 4. Use Python-level and system-level tracing

#### Python memory

Use `tracemalloc`:

```python
import tracemalloc
import gc

tracemalloc.start()

# run the scan

snapshot = tracemalloc.take_snapshot()
for stat in snapshot.statistics("lineno")[:20]:
    print(stat)
```

This tells whether memory growth is from:

- a huge list of strings;
- many `os.DirEntry` objects being retained;
- a dictionary or set that is never bounded;
- accumulated `os.walk` filename lists.

#### CPU and call stack

Use:

```bash
py-spy top -- pid_of_running_process
py-spy record -- python app.py
```

or:

```bash
cProfile app.py
```

For a hung scanner, inspect the current Python frame:

```bash
py-spy dump --pid <pid>
```

#### System calls

On Linux:

```bash
strace -f -e trace=openat,getdents64,newfstatat,lstat,readlink python app.py
```

This can show:

- repeated `getdents` calls on the same directory;
- `newfstatat` following symlinks;
- `Permission denied` from `openat`/`getdents`;
- the scanner bouncing between `/a/link -> /b`, `/b/link -> /a`, etc.

On macOS, similar tools include `fs_usage`.

---

### 5. Classify the root cause

At this point I would map observed behavior to a likely bug:

| Symptom | Likely cause |
|---|---|
| `PermissionError` crashes scan | no error handling around directory open/list/stat |
| Broken symlink crashes scan | calling `stat(follow_symlinks=True)` on broken symlinks |
| Hangs forever | symlink directory loop, often via `followlinks=True` or `os.path.isdir()` |
| `RecursionError` | recursive scanner on deep tree |
| Memory grows proportionally to total files | storing all paths/files before filtering |
| Memory grows for large directories | using `os.listdir()` or `os.walk` with filename lists for each directory |
| Missing files | swallowing errors silently or not handling filesystem errors |

---

## Robust implementation

Below is a robust iterative implementation.

Goals:

1. No recursion limit.
2. Does not follow directory symlinks by default, avoiding loops.
3. Skips unreadable directories without crashing.
4. Uses `os.scandir()` to avoid materializing full directory lists.
5. Only keeps matching files, not all files.
6. Handles broken symlinks, vanished files, permission errors, sockets, pipes, devices, etc.
7. Returns files sorted by size, largest first.

```python
from __future__ import annotations

import logging
import os
import stat
from collections.abc import Callable, Iterator
from typing import Optional, Union

GiB = 1024**3

PathLike = Union[str, bytes, os.PathLike[str], os.PathLike[bytes]]

logger = logging.getLogger(__name__)


def _log_scan_error(exc: OSError) -> None:
    """
    Default error handler.

    It logs and continues. Replace with a stricter handler if desired.
    """
    logger.warning(
        "Scan error while accessing %r: %s",
        getattr(exc, "filename", None) or "<unknown path>",
        exc,
    )


def iter_large_files_with_size(
    root: PathLike,
    *,
    min_size: int = GiB,
    follow_directory_symlinks: bool = False,
    on_error: Optional[Callable[[OSError], None]] = None,
) -> Iterator[tuple[int, Union[str, bytes]]]:
    """
    Iterate over regular files under `root` whose size is >= `min_size`.

    Yields:
        (file_size_in_bytes, file_path)

    Behavior:
    - Iterative depth-first traversal.
    - Uses os.scandir(), which does not materialize an entire directory listing.
    - By default does not follow directory symlinks, so symlink loops are avoided.
    - If follow_directory_symlinks=True, it follows directory symlinks but keeps
      a visited set based on (st_dev, st_ino) to avoid loops.
    - Only regular files are yielded.
    - File symlinks are not followed when follow_directory_symlinks=False.
    - Errors are reported through on_error and then skipped.

    Notes:
    - st_size is the apparent file size, not necessarily disk usage.
    - Hard links are treated as separate directory entries. If you need to
      deduplicate hard links, you must track inode identity, which increases
      memory usage.
    - On some filesystems, notably some Windows/network filesystems,
      (st_dev, st_ino) may not be a reliable identity. The default
      follow_directory_symlinks=False avoids that problem.
    """
    report = on_error or _log_scan_error

    try:
        root_path = os.fspath(root)
    except TypeError as exc:
        raise TypeError(f"root must be a path-like object, not {type(root)!r}") from exc

    # When not following directory symlinks, no visited set is needed.
    # When following them, remember visited directory identities.
    visited_dir_inodes: Optional[set[tuple[int, int]]] = (
        set() if follow_directory_symlinks else None
    )

    # If following symlinks, verify the root itself is a directory and mark it
    # as visited. If not following symlinks, let os.scandir() handle the error.
    if follow_directory_symlinks:
        try:
            root_stat = os.stat(root_path, follow_symlinks=True)
        except OSError as exc:
            report(exc)
            return

        if not stat.S_ISDIR(root_stat.st_mode):
            return

        visited_dir_inodes.add((root_stat.st_dev, root_stat.st_ino))

    pending_dirs: list[Union[str, bytes]] = [root_path]

    while pending_dirs:
        dirpath = pending_dirs.pop()

        try:
            dir_entries = os.scandir(dirpath)
        except OSError as exc:
            # Examples:
            # - permission denied
            # - directory disappeared during traversal
            # - network filesystem failure
            # - path is not a directory
            report(exc)
            continue

        with dir_entries:
            try:
                for entry in dir_entries:
                    try:
                        # Use lstat semantics by default.
                        # If follow_directory_symlinks=True, stat follows links.
                        st = entry.stat(follow_symlinks=follow_directory_symlinks)
                    except OSError as exc:
                        # Broken symlinks, permission problems, races where the
                        # entry disappears after readdir but before stat, etc.
                        report(exc)
                        continue

                    if stat.S_ISDIR(st.st_mode):
                        # Avoid loops when explicitly following directory symlinks.
                        if follow_directory_symlinks:
                            assert visited_dir_inodes is not None
                            inode_id = (st.st_dev, st.st_ino)

                            if inode_id in visited_dir_inodes:
                                continue

                            visited_dir_inodes.add(inode_id)

                        # entry.path avoids manually calling os.path.join(),
                        # which is slightly faster and avoids extra string copies.
                        pending_dirs.append(entry.path)

                    elif stat.S_ISREG(st.st_mode):
                        # Only regular files count as "files" here.
                        # This intentionally skips symlinks, FIFOs, sockets,
                        # devices, etc.
                        if st.st_size >= min_size:
                            yield st.st_size, entry.path

                    # Else:
                    #   symlink, fifo, socket, device, etc.
                    #   skip silently, as they are not regular files.

            except OSError as exc:
                # os.scandir() can raise while iterating, not only while opening.
                # This can happen if permissions change mid-iteration, the
                # directory is removed, or a network filesystem fails.
                report(exc)


def find_large_files(
    root: PathLike,
    *,
    min_size: int = GiB,
    follow_directory_symlinks: bool = False,
    on_error: Optional[Callable[[OSError], None]] = None,
) -> list[Union[str, bytes]]:
    """
    Return all regular files under `root` with size >= `min_size`.

    The returned paths are sorted from largest to smallest.

    The path type follows the type of `root`:
    - str root -> str paths
    - bytes root -> bytes paths
    - pathlib.Path usually yields str paths unless it is a bytes path-like

    Important memory note:
    A list of all matching files is required by the API contract. If there are
    millions of matching files, this function will consume memory proportional
    to the matching results, not proportional to the entire tree. If even the
    result set is too large to fit in memory, an external sort or streaming
    iterator API is required.
    """
    matches = list(
        iter_large_files_with_size(
            root,
            min_size=min_size,
            follow_directory_symlinks=follow_directory_symlinks,
            on_error=on_error,
        )
    )

    if not matches:
        return []

    # Sort descending by size.
    # Tuple sort uses path as a tie-breaker for equal sizes.
    # Avoiding key=... avoids building an extra list of keys internally.
    matches.sort(reverse=True)

    # Convert (size, path) tuples in-place to paths.
    # This releases the tuple and size objects gradually instead of building
    # a second list.
    for i in range(len(matches)):
        matches[i] = matches[i][1]

    return matches
```

---

## Example usage

```python
import logging

logging.basicConfig(level=logging.INFO)

try:
    files = find_large_files("/data")
except KeyboardInterrupt:
    raise
except OSError as exc:
    # If you configured on_error to raise, handle it here.
    raise
else:
    for path in files:
        print(path)
```

Custom error handling:

```python
def raise_scan_errors(exc: OSError) -> None:
    raise

files = find_large_files("/data", on_error=raise_scan_errors)
```

Skip unreadable directories silently:

```python
files = find_large_files("/data", on_error=lambda exc: None)
```

---

## Why this implementation is safer

### No recursion limit

The traversal uses an explicit stack:

```python
pending_dirs = [root_path]
```

so the process does not consume Python call frames proportional to directory depth.

---

### Unreadable directories are skipped

The code handles errors in three places:

1. Opening a directory:

```python
try:
    dir_entries = os.scandir(dirpath)
except OSError as exc:
    report(exc)
    continue
```

2. Iterating directory entries:

```python
except OSError as exc:
    report(exc)
```

This covers failures that happen during `getdents()` after the directory handle was successfully opened.

3. Stat-ing an entry:

```python
try:
    st = entry.stat(follow_symlinks=follow_directory_symlinks)
except OSError as exc:
    report(exc)
    continue
```

This covers broken symlinks, disappearing entries, and permission issues.

---

### No symlink loops by default

By default:

```python
follow_directory_symlinks = False
entry.stat(follow_symlinks=False)
```

Therefore:

- a symlink to a directory is not treated as a directory to traverse;
- a symlink to a file is not treated as a regular file unless its link itself is the target semantics you want;
- there is no risk of infinite descent through `link -> ancestor`.

If you explicitly want to follow directory symlinks:

```python
follow_directory_symlinks = True
```

the code tracks visited directories using:

```python
(st.st_dev, st.st_ino)
```

so a symlink back to an already visited directory is skipped.

---

### Memory use is bounded by pending directories and matching files

This scanner does not:

- read an entire directory into a list;
- store all file paths;
- store all files before filtering;
- recurse through Python frames for each directory.

Memory is approximately:

- pending directory paths;
- one active `os.scandir()` iterator;
- currently matching files;
- optional visited directory set if following directory symlinks.

That is substantially better than storing every path in the tree.

---

### Handles special files and races

The scanner only yields regular files:

```python
stat.S_ISREG(st.st_mode)
```

This skips:

- FIFOs/pipes;
- sockets;
- device nodes;
- symlinks;
- special filesystem objects.

It also handles common races where a file or directory disappears during scanning.

---

## Important semantic choices

### 1. 1 GiB means binary GiB

The constant used is:

```python
GiB = 1024**3
```

That is 1 gibibyte. If the requirement actually means 1 decimal gigabyte, use:

```python
min_size = 1_000_000_000
```

---

### 2. File size means `st_size`, not necessarily disk usage

`st_size` is the apparent size. This is normally what people mean by “file larger than 1 GiB.”

However, for sparse files or some filesystems, the logical size may be much larger than actual allocated disk space. If the requirement is “occupies more than 1 GiB on disk,” you may need filesystem-specific allocation size, for example via `st_blocks`:

```python
allocated_bytes = st.st_blocks * 512
```

But `st_blocks` portability and semantics are filesystem-dependent, so `st_size` is usually the safer general-purpose choice.

---

### 3. Symlinked files are not counted by default

The default implementation is strict: only real regular files are returned.

If you want to include symlinks whose targets are regular files, while still not following directory symlinks during traversal, you can extend it by stat-ing symlinks separately:

```python
if entry.is_symlink():
    try:
        target_st = entry.stat(follow_symlinks=True)
    except OSError as exc:
        report(exc)
        continue

    if stat.S_ISREG(target_st.st_mode) and target_st.st_size >= min_size:
        yield target_st.st_size, entry.path
```

But that intentionally follows symlink targets for files and can encounter broken symlinks or symlink cycles. The simpler and safer default is to skip symlinks.

---

## If the matching result set itself is too large

There is an unavoidable lower bound: if the function must return a materialized sorted list, memory usage is at least proportional to the number of matching files.

For example:

```python
list[str | bytes]
```

cannot be returned without holding all of those paths in memory.

If the tree contains millions of files larger than 1 GiB, the robust API should probably be streaming, not list-returning.

A memory-bounded approach would:

1. Scan the tree and yield `(size, path)` tuples.
2. Accumulate a bounded in-memory buffer.
3. When the buffer is too large, sort the buffer and spill it to a temporary file.
4. After scanning, perform a k-way merge of the sorted temporary files.
5. Stream the merged results.

That can be built on top of the same generator:

```python
def find_large_files_stream_sorted(
    root: PathLike,
    *,
    min_size: int = GiB,
    max_buffer_size: int = 1_000_000,
    tmp_dir: PathLike | None = None,
    follow_directory_symlinks: bool = False,
    on_error: Optional[Callable[[OSError], None]] = None,
) -> Iterator[Union[str, bytes]]:
    """
    Streaming memory-bounded variant.

    This sketch assumes you serialize chunks to temporary files, for example
    using a simple format containing:

        size + NUL + path + NUL

    or using pickle/json/protocol buffers if paths can contain arbitrary bytes.

    The actual implementation should use a safe serialization format that
    preserves bytes paths and path separators exactly.
    """
    raise NotImplementedError(
        "Implement external merge sort here if results do not fit in memory."
    )
```

For many practical workloads, however, the main problem is not millions of matching large files; it is the scanner storing every file or every directory listing. The generator-based scanner above fixes that case.

---

## Tests I would add

I would write tests around the failure modes.

### Test unreadable directory does not crash

```python
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
```

Be careful: as root, permission changes may not deny access in the same way as a normal user.

---

### Test symlink loop terminates

```python
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
```

The test should complete instead of hanging or raising `RecursionError`.

---

### Test only files above threshold are returned

```python
from pathlib import Path

def test_threshold(tmp_path):
    small = tmp_path / "small"
    big = tmp_path / "big"

    small.write_bytes(b"\0" * 1024)
    big.write_bytes(b"\0" * (2 * 1024**3))

    found = find_large_files(tmp_path)

    assert str(big) in [str(p) for p in found]
    assert str(small) not in [str(p) for p in found]
```

---

### Test sorting order

```python
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
```

---

## Summary

The diagnosis would focus on three things:

1. **Reproduce** unreadable directories, symlink loops, and large trees separately.
2. **Observe** exceptions, system calls, and memory allocations before changing behavior.
3. **Map** the symptoms to likely implementation flaws: unhandled `OSError`, symlink-following recursion, and over-collection of paths or directory listings.

The robust fix is an iterative `os.scandir()`-based scanner that:

- does not use Python recursion;
- reports or skips filesystem errors;
- avoids following directory symlinks by default;
- detects loops if symlinks are followed deliberately;
- keeps only matching files in memory;
- returns matching files sorted largest to smallest.
