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
