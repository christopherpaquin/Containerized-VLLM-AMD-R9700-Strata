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
