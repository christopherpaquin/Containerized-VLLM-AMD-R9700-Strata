import heapq
import pickle
import tempfile


def iter_sorted_large_files_externally(
    root: str | os.PathLike[str],
    *,
    min_size: int = GIB,
    max_buffered: int = 100_000,
    temp_dir: str | None = None,
):
    buffer = []
    temp_files = []

    def flush():
        if not buffer:
            return

        buffer.sort(key=lambda item: (-item[0], item[1]))

        with tempfile.NamedTemporaryFile(
            "wb",
            dir=temp_dir,
            delete=False,
            suffix=".largefiles",
        ) as f:
            path = f.name
            for size, name in buffer:
                pickle.dump((size, name), f, protocol=5)

        temp_files.append(path)
        buffer.clear()

    try:
        for item in iter_large_files(root, min_size=min_size):
            buffer.append(item)
            if len(buffer) >= max_buffered:
                flush()

        if not temp_files:
            buffer.sort(key=lambda item: (-item[0], item[1]))
            for _, path in buffer:
                yield path
            return

        flush()

        def read_file(path):
            with open(path, "rb") as f:
                while True:
                    try:
                        yield pickle.load(f)
                    except EOFError:
                        break

        chunk_iterators = [read_file(path) for path in temp_files]

        for _, path in heapq.merge(
            *chunk_iterators,
            key=lambda item: -item[0],
        ):
            yield path

    finally:
        for path in temp_files:
            try:
                os.remove(path)
            except OSError:
                pass
