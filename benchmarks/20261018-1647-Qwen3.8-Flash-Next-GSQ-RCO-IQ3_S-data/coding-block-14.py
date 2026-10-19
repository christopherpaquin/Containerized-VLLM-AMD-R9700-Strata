import heapq


def find_largest_files(
    root: str,
    *,
    limit: int = 100,
    min_bytes_exclusive: int = GiB,
    follow_symlinks: bool = False,
) -> list[str]:
    """
    Return the largest files larger than 1 GiB, limited to at most `limit`
    results.

    This keeps only O(limit) results in memory plus the scan stack.
    """

    heap: list[tuple[int, str]] = []

    for size, path in iter_large_files(
        root,
        min_bytes_exclusive=min_bytes_exclusive,
        follow_symlinks=follow_symlinks,
    ):
        # Store as (size, path). For largest N, use a min-heap of current top N.
        if len(heap) < limit:
            heapq.heappush(heap, (size, path))
        elif size > heap[0][0]:
            heapq.heapreplace(heap, (size, path))

    # Largest to smallest.
    heap.sort(key=lambda pair: (-pair[0], pair[1]))
    return [path for _, path in heap]
