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
