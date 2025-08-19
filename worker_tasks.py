# worker_tasks.py
import time

def test_task(x, y):
    """A tiny job the worker can run."""
    time.sleep(2)  # simulate work
    result = x + y
    print(f"[worker] test_task({x}, {y}) = {result}")
    return result