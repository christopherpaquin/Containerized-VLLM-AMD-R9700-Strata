os.walk(root, followlinks=True)
Path(root).rglob("*")
os.path.isdir(child)
os.stat(child)
results.append(child)  # before size filtering
