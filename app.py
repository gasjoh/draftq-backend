import pandas as pd
import os
import uuid
from flask import Flask, request, jsonify
from flask_cors import CORS

def generate_boq_dataframe(upload_path: str) -> pd.DataFrame:
    # TODO: replace with real parsing. For now it just returns a valid BOQ table.
    data = [
        {"Item": 1, "Description": "Site setup & protection", "Unit": "LS", "Qty": 1,  "Rate": 2500.0},
        {"Item": 2, "Description": "Blockwork (200mm)",       "Unit": "m²", "Qty": 75, "Rate": 95.0},
        {"Item": 3, "Description": "Plaster to block walls",  "Unit": "m²", "Qty": 75, "Rate": 35.0},
        {"Item": 4, "Description": "Floor tiling 600×600",    "Unit": "m²", "Qty": 60, "Rate": 120.0},
        {"Item": 5, "Description": "Skirting 100mm",          "Unit": "lm", "Qty": 40, "Rate": 18.0},
        {"Item": 6, "Description": "Ceiling paint (2 coats)", "Unit": "m²", "Qty": 60, "Rate": 22.0},
    ]
    df = pd.DataFrame(data)
    df["Amount"] = df["Qty"] * df["Rate"]
    return df[["Item", "Description", "Unit", "Qty", "Rate", "Amount"]]

def write_boq_xlsx_to_bytes(df: pd.DataFrame) -> bytes:
    with io.BytesIO() as buffer:
        with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
            df.to_excel(writer, index=False, sheet_name="BOQ")
            ws = writer.sheets["BOQ"]
            last_row = len(df) + 1
            ws.write(last_row, 4, "TOTAL")
            ws.write_formula(last_row, 5, f"=SUM(F2:F{last_row})")
        return buffer.getvalue()

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
    # Protect (optional, but you have no token now)
    if not _auth_ok():
        return jsonify(ok=False, error="unauthorized"), 401

    # 1) Try JSON body (Elementor Webhook style)
    data = request.get_json(silent=True) or {}
    print("DEBUG incoming JSON:", data)
    email = data.get("email") or data.get("Email")
    file_url = data.get("uploaded_file") or data.get("upload")

    if email and file_url:
        import os, uuid, requests
        local_path = f"/tmp/{uuid.uuid4()}.pdf"
        r = requests.get(file_url, timeout=30)
        if r.status_code != 200:
            return jsonify(ok=False, error="failed to download file"), 400
        with open(local_path, "wb") as f:
            f.write(r.content)

        # enqueue job with local_path + email
        # enqueue_process(local_path, email)
        return jsonify(ok=True, queued=True), 202

    # 2) Fallback to multipart form (old style)
    email = request.form.get("email")
    file = request.files.get("file")

    if not email:
        return jsonify(ok=False, error="email required"), 400
    if not file:
        return jsonify(ok=False, error="file required"), 400

    local_path = f"/tmp/{uuid.uuid4()}_{file.filename}"
    file.save(local_path)

    # enqueue job with local_path + email
    # enqueue_process(local_path, email)
    return jsonify(ok=True, queued=True), 202
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