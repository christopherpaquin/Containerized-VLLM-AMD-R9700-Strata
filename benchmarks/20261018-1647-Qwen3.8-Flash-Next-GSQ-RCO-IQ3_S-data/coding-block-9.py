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
