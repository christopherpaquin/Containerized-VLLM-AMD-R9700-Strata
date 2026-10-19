from __future__ import annotations

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
