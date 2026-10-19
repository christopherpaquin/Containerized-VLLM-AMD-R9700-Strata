def raise_scan_errors(exc: OSError) -> None:
    raise

files = find_large_files("/data", on_error=raise_scan_errors)
