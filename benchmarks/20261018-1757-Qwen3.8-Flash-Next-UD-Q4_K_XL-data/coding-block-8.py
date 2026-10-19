try:
    iterator = os.scandir(dirpath)
except OSError as exc:
    on_error(dirpath, exc)
    continue
