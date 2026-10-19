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
