for root, dirs, files in os.walk(start, followlinks=False):
    for f in files:
        size = os.stat(os.path.join(root, f)).st_size  # raises on broken/missing file
