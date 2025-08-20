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
redis_conn = Redis.from_url(os.environ["REDIS_URL"])
q = Queue("default", connection=redis_conn)
from flask import request

from flask import request, jsonify
import os

@app.route("/process_layout", methods=["POST"])
def process_layout():
    try:
        # Debug: log what keys arrived
        print("[WEB] files keys:", list(request.files.keys()))
        print("[WEB] form keys:", list(request.form.keys()))

        # Accept common field names
        uploaded_file = (
            request.files.get("file")
            or request.files.get("upload")
            or request.files.get("document")
            or (next(iter(request.files.values()), None) if request.files else None)
        )

        if not uploaded_file:
            return jsonify(ok=False, error="No file uploaded", hint="Expect field name 'file'"), 400

        save_path = os.path.join("/tmp", uploaded_file.filename)
        uploaded_file.save(save_path)

        job = q.enqueue("worker_tasks.process_layout_task", save_path)
        return jsonify(ok=True, job_id=job.get_id(), path=save_path), 200

    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500

# ---------------------------
# Test route to enqueue a job
# ---------------------------
@app.route("/enqueue-test", methods=["GET"])
def enqueue_test():
    try:
        # sanity: check if Redis connection is alive
        redis_conn.ping()
    except Exception as e:
        return jsonify(ok=False, where="redis_conn.ping()", error=str(e)), 500

    try:
        job = q.enqueue(test_task, 2, 3)
        return jsonify(ok=True, job_id=job.get_id())
    except Exception as e:
        return jsonify(ok=False, where="q.enqueue()", error=str(e)), 500
@app.get("/debug-redis")
def debug_redis():
    try:
        # minimal calls to avoid huge output
        pong = redis_conn.ping()
        server = redis_conn.info(section="server")
        return jsonify(ok=True, ping=pong, server_version=server.get("redis_version"))
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500
if __name__ == "__main__":
    # Not used on Render (we run via gunicorn), but handy for local tests
    app.run(host="0.0.0.0", port=5000)