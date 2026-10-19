from __future__ import annotations

import logging
import os
import stat
from collections.abc import Callable, Iterator


GiB = 1 << 30

logger = logging.getLogger(__name__)


def _default_on_error(exc: OSError, path: str) -> None:
    logger.warning("Skipping %s: %s", path, exc)


def iter_large_files(
    root: str,
    *,
    min_bytes_exclusive: int = GiB,
    follow_symlinks: bool = False,
    on_error: Callable[[OSError, str], None] = _default_on_error,
) -> Iterator[tuple[int, str]]:
    """
    Yield (size_bytes, path) for regular files strictly larger than
    min_bytes_exclusive.

    By default this does not follow symlinks, which prevents symlink loops.

    If follow_symlinks=True, directory symlinks are followed but cycles are
    protected by remembering visited directories using (st_dev, st_ino).

    Errors are reported through on_error and do not abort the whole scan.
    """

    # Identity of directories already processed when following symlinks.
    visited_dirs: set[tuple[int, int]] = set()

    stack: list[str] = [root]

    while stack:
        dir_path = stack.pop()

        # If we are following symlinks, resolve this directory and check
        # for cycles before descending into it.
        if follow_symlinks:
            try:
                dir_st = os.stat(dir_path, follow_symlinks=True)
            except OSError as exc:
                on_error(exc, dir_path)
                continue

            if not stat.S_ISDIR(dir_st.st_mode):
                # This can happen if root was a regular file or symlink to file.
                # It can also happen if the path changed since it was queued.
                if (
                    stat.S_ISREG(dir_st.st_mode)
                    and dir_st.st_size > min_bytes_exclusive
                ):
                    yield dir_st.st_size, dir_path
                continue

            identity = (dir_st.st_dev, dir_st.st_ino)
            if identity in visited_dirs:
                continue
            visited_dirs.add(identity)

        try:
            with os.scandir(dir_path) as entries:
                for entry in entries:
                    try:
                        entry_st = entry.stat(
                            follow_symlinks=follow_symlinks
                        )
                    except OSError as exc:
                        # Broken symlink, permission problem, race condition, etc.
                        on_error(exc, entry.path)
                        continue

                    if stat.S_ISDIR(entry_st.st_mode):
                        stack.append(entry.path)
                        continue

                    if (
                        stat.S_ISREG(entry_st.st_mode)
                        and entry_st.st_size > min_bytes_exclusive
                    ):
                        yield entry_st.st_size, entry.path

        except NotADirectoryError as exc:
            # `root` may have been a regular file. In that case we can still
            # check whether it qualifies.
            try:
                st = os.stat(dir_path, follow_symlinks=follow_symlinks)
            except OSError as exc2:
                on_error(exc2, dir_path)
                continue

            if stat.S_ISREG(st.st_mode) and st.st_size > min_bytes_exclusive:
                yield st.st_size, dir_path

        except OSError as exc:
            # Unreadable directory, disconnected filesystem, etc.
            on_error(exc, dir_path)


def find_large_files(
    root: str,
    *,
    min_bytes_exclusive: int = GiB,
    follow_symlinks: bool = False,
) -> list[str]:
    """
    Return a list of regular file paths larger than 1 GiB, sorted from
    largest to smallest.

    This stores all matching results in memory. If there are millions of
    matching files, use the external-sort version below or process results
    incrementally.
    """

    matches: list[tuple[int, str]] = []

    for size, path in iter_large_files(
        root,
        min_bytes_exclusive=min_bytes_exclusive,
        follow_symlinks=follow_symlinks,
    ):
        matches.append((size, path))

    # Largest first. Path is used only to make tie order deterministic.
    matches.sort(key=lambda pair: (-pair[0], pair[1]))

    return [path for _, path in matches]



import heapq
import logging
import os
import pickle
import tempfile
from collections.abc import Iterator
from contextlib import ExitStack


GiB = 1 << 30

logger = logging.getLogger(__name__)


def _iter_pickle_objects(file) -> Iterator[tuple[int, str]]:
    """
    Read a stream of pickled objects from a binary file.
    """
    while True:
        try:
            yield pickle.load(file)
        except EOFError:
            return


def iter_large_files_sorted_stream(
    root: str,
    *,
    min_bytes_exclusive: int = GiB,
    follow_symlinks: bool = False,
    max_chunk_entries: int = 100_000,
) -> Iterator[str]:
    """
    Yield file paths larger than 1 GiB sorted from largest to smallest.

    This avoids holding all results in memory at once by writing sorted
    temporary chunks and then merging them. The final yielded output is still
    sorted, but the generator does not require all matches in RAM simultaneously.
    """

    def compare_key(pair: tuple[int, str]) -> tuple[int, str]:
        # Sorting ascending by this key yields largest size first because
        # -size makes larger sizes sort earlier.
        return (-pair[0], pair[1])

    with tempfile.TemporaryDirectory(prefix="large_files_sort_") as tmpdir:
        chunk: list[tuple[int, str]] = []
        chunk_files: list[str] = []

        def flush_chunk() -> None:
            if not chunk:
                return

            chunk.sort(key=compare_key)

            chunk_path = os.path.join(
                tmpdir,
                f"chunk_{len(chunk_files):06d}.pickle",
            )

            with open(chunk_path, "wb") as fh:
                for size, path in chunk:
                    pickle.dump((size, path), fh)

            chunk_files.append(chunk_path)
            chunk.clear()

        # Scan and build sorted chunks.
        for item in iter_large_files(
            root,
            min_bytes_exclusive=min_bytes_exclusive,
            follow_symlinks=follow_symlinks,
        ):
            chunk.append(item)

            if len(chunk) >= max_chunk_entries:
                flush_chunk()

        flush_chunk()

        if not chunk_files:
            return

        # Merge all sorted chunks lazily.
        with ExitStack() as stack:
            readers = []

            for path in chunk_files:
                fh = stack.enter_context(open(path, "rb"))
                readers.append(_iter_pickle_objects(fh))

            # Each reader is already sorted by compare_key ascending.
            for size, path in heapq.merge(
                *readers,
                key=compare_key,
            ):
                yield path


def find_large_files_sorted_stream_to_list(
    root: str,
    *,
    min_bytes_exclusive: int = GiB,
    follow_symlinks: bool = False,
    max_chunk_entries: int = 100_000,
) -> list[str]:
    """
    Convenience wrapper.

    This still returns a list in memory, but it reduces peak sorting memory
    compared to sorting one giant list entirely in RAM. It uses temporary disk
    chunks.

    If the final list itself does not fit in memory, do not materialize it;
    consume iter_large_files_sorted_stream(...) directly.
    """

    return [
        path
        for path in iter_large_files_sorted_stream(
            root,
            min_bytes_exclusive=min_bytes_exclusive,
            follow_symlinks=follow_symlinks,
            max_chunk_entries=max_chunk_entries,
        )
    ]
