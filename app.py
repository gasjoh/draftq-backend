# app.py
from flask import Flask, jsonify
from redis import Redis
from rq import Queue
import os

# Import the worker task (make sure worker_tasks.py has test_task)
from worker_tasks import test_task

app = Flask(__name__)

# ---------------------------
# Health check endpoints
# ---------------------------
@app.get("/health")
def health():
    return jsonify(ok=True)

@app.get("/health/redis")
def health_redis():
    try:
        r = Redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            ssl=True,
            ssl_cert_reqs=None
        )
        r.ping()
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500

# ---------------------------
# RQ setup (connect to Redis)
# ---------------------------
redis_conn = Redis.from_url(
    os.environ["REDIS_URL"],
    ssl=True,
    ssl_cert_reqs=None
)
q = Queue("default", connection=redis_conn)

# ---------------------------
# Test route to enqueue a job
# ---------------------------
@app.get("/enqueue-test")
def enqueue_test():
    job = q.enqueue(test_task, 2, 3)  # small dummy job
    return jsonify(ok=True, job_id=job.get_id())

if __name__ == "__main__":
    # Not used on Render (we run via gunicorn), but handy for local tests
    app.run(host="0.0.0.0", port=5000)