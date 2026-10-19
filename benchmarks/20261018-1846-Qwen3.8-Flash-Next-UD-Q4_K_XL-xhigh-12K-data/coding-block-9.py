import tracemalloc
import gc

tracemalloc.start()

# run the scan

snapshot = tracemalloc.take_snapshot()
for stat in snapshot.statistics("lineno")[:20]:
    print(stat)
