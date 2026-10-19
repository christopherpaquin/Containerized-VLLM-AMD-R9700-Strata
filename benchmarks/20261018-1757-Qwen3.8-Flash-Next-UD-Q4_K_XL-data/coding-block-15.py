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
