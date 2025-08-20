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
import os, tempfile

@app.route("/process_layout", methods=["POST"])
def process_layout():
    try:
        print("[WEB] files keys:", list(request.files.keys()))
        print("[WEB] form keys:", list(request.form.keys()))
        # Try 1: multipart upload (frontend/curl)
        uploaded_file = (
            request.files.get("file")
            or request.files.get("upload")
            or (next(iter(request.files.values()), None) if request.files else None)
        )
        save_path = None

        if uploaded_file:
            # Save direct upload to /tmp
            save_path = os.path.join("/tmp", uploaded_file.filename)
            uploaded_file.save(save_path)
            print(f"[WEB] saved multipart file -> {save_path}")

        else:
            # Try 2: Elementor webhook JSON with a link
            data = request.get_json(silent=True) or {}
            print("[WEB] JSON keys:", list(data.keys()))

            # Elementor can send: {"file": "https://...pdf"} OR {"file": ["https://...pdf"]}
            # Some setups use "file_url" or the field ID as the key.
            candidate = (
                data.get("file")
                or data.get("file_url")
                or data.get("File")
                or data.get("document")
                or data.get("upload")
            )

            # If the value is a list, take the first item
            if isinstance(candidate, list) and candidate:
                candidate = candidate[0]

            if isinstance(candidate, str) and candidate.startswith("http"):
                import requests
                # Download to /tmp
                resp = requests.get(candidate, timeout=30)
                resp.raise_for_status()
                # Derive a filename
                filename = candidate.split("/")[-1] or "uploaded.pdf"
                save_path = os.path.join("/tmp", filename)
                with open(save_path, "wb") as f:
                    f.write(resp.content)
                print(f"[WEB] fetched file from URL -> {save_path}")
            else:
                # If Elementor sends fields nested under 'fields' or similar, scan for a PDF URL
                if isinstance(data, dict):
                    pdf_url = None
                    for v in data.values():
                        if isinstance(v, str) and v.lower().endswith(".pdf") and v.startswith("http"):
                            pdf_url = v; break
                        if isinstance(v, list):
                            for item in v:
                                if isinstance(item, str) and item.lower().endswith(".pdf") and item.startswith("http"):
                                    pdf_url = item; break
                            if pdf_url: break
                    if pdf_url:
                        import requests
                        resp = requests.get(pdf_url, timeout=30)
                        resp.raise_for_status()
                        filename = pdf_url.split("/")[-1] or "uploaded.pdf"
                        save_path = os.path.join("/tmp", filename)
                        with open(save_path, "wb") as f:
                            f.write(resp.content)
                        print(f"[WEB] fetched file from scanned URL -> {save_path}")

        if not save_path or not os.path.exists(save_path):
            return jsonify(ok=False, error="No file found (multipart or URL)"), 400

        # Enqueue worker
        job = q.enqueue("worker_tasks.process_layout_task", save_path)
        return jsonify(ok=True, job_id=job.get_id(), path=save_path), 200

    except Exception as e:
        # Log full error to Render logs, return friendly json
        print("[WEB][ERROR]", repr(e))
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