try:
    st = entry.stat(follow_symlinks=follow_directory_symlinks)
except OSError as exc:
    report(exc)
    continue
