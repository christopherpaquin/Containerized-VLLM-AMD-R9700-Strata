import traceback
import logging

logging.basicConfig(level=logging.DEBUG)

try:
    result = scan_large_files(root)
except Exception:
    logging.exception("scan_large_files failed on %s", root)
    raise
