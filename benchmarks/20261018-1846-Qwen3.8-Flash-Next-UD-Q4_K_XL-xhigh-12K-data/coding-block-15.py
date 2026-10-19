try:
    dir_entries = os.scandir(dirpath)
except OSError as exc:
    report(exc)
    continue
