try:
    os.scandir(path)
except OSError as exc:
    logger.warning("Cannot open directory %r: %s", path, exc)
