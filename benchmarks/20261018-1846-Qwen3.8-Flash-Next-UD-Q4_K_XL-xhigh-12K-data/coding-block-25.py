if entry.is_symlink():
    try:
        target_st = entry.stat(follow_symlinks=True)
    except OSError as exc:
        report(exc)
        continue

    if stat.S_ISREG(target_st.st_mode) and target_st.st_size >= min_size:
        yield target_st.st_size, entry.path
