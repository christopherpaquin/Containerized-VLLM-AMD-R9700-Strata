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
