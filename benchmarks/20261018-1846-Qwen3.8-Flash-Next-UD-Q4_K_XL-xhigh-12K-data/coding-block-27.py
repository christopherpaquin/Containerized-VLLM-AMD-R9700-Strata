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
