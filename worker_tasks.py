# worker_tasks.py
import time

def test_task(x, y):
    """A tiny job the worker can run."""
    time.sleep(2)  # simulate work
    result = x + y
    print(f"[worker] test_task({x}, {y}) = {result}")
    return result
    
def process_layout_task(file_path: str):
    import os, time
    time.sleep(2)
    size = os.path.getsize(file_path) if os.path.exists(file_path) else -1
    print(f"[worker] process_layout_task file={file_path} size={size} bytes")
    return {"file": file_path, "size_bytes": size}