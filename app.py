import os
import uuid
from flask import Flask, request, jsonify
from flask_cors import CORS

# Queue
from rq import Queue
import redis

app = Flask(__name__)
CORS(app)

UPLOAD_FOLDER = "/tmp"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Redis connection
REDIS_URL = os.environ.get("REDIS_URL")
if not REDIS_URL:
    raise RuntimeError("REDIS_URL is not set")
redis_conn = redis.from_url(REDIS_URL)
q = Queue("draftq", connection=redis_conn)

def _auth_ok():
    expected = os.environ.get("DRAFTQ_TOKEN", "")
    return (not expected) or (request.headers.get("X-DRAFTQ-TOKEN") == expected)

@app.route("/", methods=["GET"])
def home():
    return jsonify(message="DraftQ backend is running")

@app.route("/health", methods=["GET"])
def health():
    return jsonify(ok=True)

@app.route("/process_layout", methods=["POST"])
def process_layout():
    # Protect (optional)
    if not _auth_ok():
        return jsonify(ok=False, error="unauthorized"), 401

    # Accept multipart form: email + file
    email = request.form.get("email")
    file = request.files.get("file")

    if not email:
        return jsonify(ok=False, error="email required"), 400
    if not file:
        return jsonify(ok=False, error="file required (multipart field 'file')"), 400

    # Save upload to /tmp
    ext = os.path.splitext(file.filename or "layout")[1] or ".pdf"
    job_id = str(uuid.uuid4())
    save_path = os.path.join(UPLOAD_FOLDER, f"{job_id}{ext}")
    file.save(save_path)

    # Enqueue the job in the worker
    from worker_tasks import process_layout_job
    job = q.enqueue(process_layout_job, save_path, email, job_id=job_id, ttl=60*60*6)  # 6h

    return jsonify(ok=True, job_id=job.get_id())