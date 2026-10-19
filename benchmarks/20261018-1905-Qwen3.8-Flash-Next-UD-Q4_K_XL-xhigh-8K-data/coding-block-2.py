import sys

def log_error(exc: OSError, path: str | None) -> None:
    print(f"skipping {path!r}: {exc}", file=sys.stderr)

paths = files_larger_than_1gib("/data", on_error=log_error)

for path in paths:
    print(path)
