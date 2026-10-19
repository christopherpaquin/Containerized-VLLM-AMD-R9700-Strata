os.scandir(path)          # raises OSError if path is unreadable
for name in os.listdir(path):
    path = os.path.join(path, name)
    if os.path.isdir(path):  # may raise or return False depending on case
        scan(...)
