import os
import logging

logger = logging.getLogger("large_files")

def inspect_entry(entry):
    logger.debug("Entry: %r", entry.path)
    if entry.is_symlink():
        logger.debug("Symlink target: %r", os.readlink(entry.path))
