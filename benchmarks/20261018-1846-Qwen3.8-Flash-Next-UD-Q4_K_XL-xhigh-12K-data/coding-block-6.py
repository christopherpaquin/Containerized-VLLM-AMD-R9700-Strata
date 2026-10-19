import logging
import traceback

logger = logging.getLogger("large_files")

def scan(root):
    logger.info("Scanning %r", root)
    try:
        ...  # original scan logic
    except Exception:
        logger.exception("Scan failed while scanning %r", root)
        raise
