visited_real_paths = set()
real = os.path.realpath(path)
if real in visited_real_paths:
    continue
