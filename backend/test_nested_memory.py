
import os
import threading
import time
import psutil

from pathlib import Path
from app.services.nested_geometry_reader import iter_drawing_pages


source = Path("data/validation_stage5/drawing_1002_complete_test.json")
process = psutil.Process(os.getpid())

baseline = process.memory_info().rss / (1024 * 1024)
peak_memory = [baseline]
stop_monitor = threading.Event()


def monitor_memory():
    while not stop_monitor.is_set():
        current_memory = (
            process.memory_info().rss / (1024 * 1024)
        )

        peak_memory[0] = max(
            peak_memory[0],
            current_memory
        )

        time.sleep(0.01)


monitor = threading.Thread(target=monitor_memory)
monitor.start()

total = 0

try:
    for page in iter_drawing_pages(source):
        for primitive in page["primitives"]:
            total += 1
finally:
    stop_monitor.set()
    monitor.join()


final_memory = process.memory_info().rss / (1024 * 1024)

print("\n========== MEMORY BENCHMARK ==========")
print("Primitives processed:", total)
print(f"Baseline memory: {baseline:.2f} MB")
print(f"Peak memory: {peak_memory[0]:.2f} MB")
print(f"Final memory: {final_memory:.2f} MB")
print(
    f"Peak memory increase: "
    f"{peak_memory[0] - baseline:.2f} MB"
)
print("======================================")
