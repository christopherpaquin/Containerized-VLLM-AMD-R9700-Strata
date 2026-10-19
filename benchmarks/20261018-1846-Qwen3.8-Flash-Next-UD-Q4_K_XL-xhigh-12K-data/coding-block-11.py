import logging

logging.basicConfig(level=logging.INFO)

try:
    files = find_large_files("/data")
except KeyboardInterrupt:
    raise
except OSError as exc:
    # If you configured on_error to raise, handle it here.
    raise
else:
    for path in files:
        print(path)
