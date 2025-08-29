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
    if not _auth_ok():
        return jsonify(ok=False, error="unauthorized"), 401

    import os, uuid, requests, json

    # ---- helpers ----
    def first_url(v):
        # handles: string, list[str|dict], dict with 'url'
        if isinstance(v, str):
            return v
        if isinstance(v, list) and v:
            return first_url(v[0])
        if isinstance(v, dict):
            if "url" in v and isinstance(v["url"], str):
                return v["url"]
            # common Elementor file object shapes
            for k in ("file", "value", "values"):
                if k in v:
                    return first_url(v[k])
        return None

    def get_any(d, *names):
        # try exact keys (case-insensitive)
        for n in names:
            for k, v in (d.items() if isinstance(d, dict) else []):
                if k.lower() == n.lower():
                    return v
        return None

    def find_by_substring(d, *subs):
        # last-resort: look for a key containing any substring
        for k, v in (d.items() if isinstance(d, dict) else []):
            kl = k.lower()
            if any(s in kl for s in subs):
                return v
        return None

    # ---- inspect request (tiny, safe logs) ----
    ct = request.headers.get("Content-Type", "")
    raw_preview = request.get_data(as_text=True)[:400]
    print("DEBUG ct:", ct)
    print("DEBUG raw_preview:", raw_preview)

    # ---- 1) Try JSON (Elementor Advanced Data often sends JSON) ----
    data = request.get_json(silent=True) or {}

    # Elementor sometimes nests under 'form_fields' or 'fields' (list or dict)
    form_fields = data.get("form_fields") or data.get("fields") or {}
    if isinstance(form_fields, list):
        # convert list[{id,label,value,url,...}] -> dict[id]=value/url/values
        ff = {}
        for item in form_fields:
            if isinstance(item, dict) and "id" in item:
                ff[item["id"]] = item.get("value") or item.get("url") or item.get("values") or item.get("file")
        form_fields = ff

    # candidates from JSON
    email = get_any(data, "email", "Email") or get_any(form_fields, "email", "Email")
    file_val = (
        get_any(data, "uploaded_file", "file_url", "upload", "Upload")
        or get_any(form_fields, "uploaded_file", "file_url", "upload", "Upload")
        or find_by_substring(data, "file", "upload")
        or find_by_substring(form_fields, "file", "upload")
    )
    file_url = first_url(file_val)

    # ---- 2) If not JSON, try form-encoded (application/x-www-form-urlencoded) ----
    if not (email and file_url):
        form = request.form.to_dict(flat=False)
        # flatten singletons
        flat = {k: (v[0] if isinstance(v, list) and v else v) for k, v in form.items()}
        print("DEBUG form keys:", list(flat.keys()))

        email = email or get_any(flat, "email", "Email") or find_by_substring(flat, "email")
        fv = get_any(flat, "uploaded_file", "file_url", "upload", "Upload") or find_by_substring(flat, "file", "upload")
        file_url = file_url or first_url(fv)

    # ---- 3) Fallback to multipart file (old flow) ----
    file_storage = request.files.get("file")

    if not file_storage and not (email and file_url):
        return jsonify(ok=False, error="missing file or (email+file_url)"), 400

    # ---- 4) Materialize to /tmp ----
    if file_storage:
        local_path = f"/tmp/{uuid.uuid4()}_{file_storage.filename}"
        file_storage.save(local_path)
        print("DEBUG saved multipart file:", local_path)
    else:
        local_path = f"/tmp/{uuid.uuid4()}.pdf"
        try:
            r = requests.get(file_url, timeout=45)
        except Exception as e:
            return jsonify(ok=False, error=f"download error: {e}"), 400
        if r.status_code != 200 or not r.content:
            return jsonify(ok=False, error=f"failed to download file: {file_url}"), 400
        with open(local_path, "wb") as f:
            f.write(r.content)
        print("DEBUG downloaded URL to:", local_path)

    # ---- 5) Enqueue job (hook to your existing function) ----
    # enqueue_process(local_path, email)  # <- your existing queue call

    return jsonify(ok=True, queued=True), 202