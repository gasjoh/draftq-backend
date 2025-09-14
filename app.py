# app.py
# -*- coding: utf-8 -*-

import os
import io
import uuid
import requests
from typing import Any, Dict, Optional

import pandas as pd
from flask import Flask, request, jsonify
from flask_cors import CORS

# ---------- Optional queue (won't crash if REDIS_URL is missing) ----------
try:
    from rq import Queue
    import redis  # type: ignore

    REDIS_URL = os.environ.get("REDIS_URL", "")
    redis_conn = redis.from_url(REDIS_URL) if REDIS_URL else None
    q: Optional[Queue] = Queue("draftq", connection=redis_conn) if redis_conn else None
except Exception:
    REDIS_URL = ""
    q = None

# ---------- App ----------
app = Flask(__name__)
CORS(app)

UPLOAD_FOLDER = "/tmp"  # Render’s writable dir
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ---------- BOQ helpers (placeholder logic) ----------
def generate_boq_dataframe(upload_path: str) -> pd.DataFrame:
    # NOTE: 'upload_path' is unused in the placeholder; keep signature for future pipeline.
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

# ---------- Auth ----------
def _auth_ok() -> bool:
    expected = os.environ.get("DRAFTQ_TOKEN", "")
    return (not expected) or (request.headers.get("X-DRAFTQ-TOKEN") == expected)

# ---------- Small helpers for flexible Elementor payloads ----------
def _first_url(v: Any) -> Optional[str]:
    if isinstance(v, str):
        return v
    if isinstance(v, list) and v:
        return _first_url(v[0])
    if isinstance(v, dict):
        if isinstance(v.get("url"), str):
            return v["url"]
        for k in ("file", "value", "values"):
            if k in v:
                return _first_url(v[k])
    return None

def _get_any(d: Any, *names: str) -> Optional[Any]:
    if not isinstance(d, dict):
        return None
    lowered = {str(k).lower(): v for k, v in d.items()}
    for n in names:
        v = lowered.get(n.lower())
        if v is not None:
            return v
    return None

def _find_by_substring(d: Any, *subs: str) -> Optional[Any]:
    if not isinstance(d, dict):
        return None
    for k, v in d.items():
        kl = str(k).lower()
        if any(s in kl for s in subs):
            return v
    return None

# ---------- Routes ----------
@app.get("/")
def home():
    return jsonify(message="DraftQ backend is running"), 200

@app.get("/health")
def health():
    return "ok", 200

@app.post("/process_layout")
def process_layout():
    if not _auth_ok():
        return jsonify(ok=False, error="unauthorized"), 401

    # ---- light debugging ----
    ct = request.headers.get("Content-Type", "")
    print("DEBUG Content-Type:", ct)

    # ---- 1) Try JSON body ----
    data = request.get_json(silent=True) or {}
    form_fields = data.get("form_fields") or data.get("fields") or {}

    if isinstance(form_fields, list):
        tmp: Dict[str, Any] = {}
        for item in form_fields:
            if isinstance(item, dict) and "id" in item:
                tmp[str(item["id"])] = (
                    item.get("value")
                    or item.get("url")
                    or item.get("values")
                    or item.get("file")
                )
        form_fields = tmp

    email = _get_any(data, "email", "Email") or _get_any(form_fields, "email", "Email")
    file_val = (
        _get_any(data, "uploaded_file", "file_url", "upload", "Upload")
        or _get_any(form_fields, "uploaded_file", "file_url", "upload", "Upload")
        or _find_by_substring(data, "file", "upload")
        or _find_by_substring(form_fields, "file", "upload")
    )
    file_url = _first_url(file_val)

    # ---- 2) If not JSON, try form-urlencoded ----
    if not (email and file_url):
        form = request.form.to_dict(flat=False)
        flat = {k: (v[0] if isinstance(v, list) and v else v) for k, v in form.items()}
        if not email:
            email = _get_any(flat, "email", "Email") or _find_by_substring(flat, "email")
        if not file_url:
            fv = (
                _get_any(flat, "uploaded_file", "file_url", "upload", "Upload")
                or _find_by_substring(flat, "file", "upload")
            )
            if not fv:
                for k, v in flat.items():
                    if isinstance(k, str) and k.lower().endswith((".pdf", ".dwg", ".png", ".jpg", ".jpeg")):
                        fv = v
                        break
            file_url = _first_url(fv)

    # ---- 3) Fallback to multipart file ----
    file_storage = request.files.get("file") or request.files.get("upload")

    if not file_storage and not (email and file_url):
        return jsonify(ok=False, error="missing file or (email+file_url)"), 400

    # ---- 4) Save to /tmp ----
    if file_storage:
        safe_name = os.path.basename(file_storage.filename or "upload")
        local_path = os.path.join(UPLOAD_FOLDER, f"{uuid.uuid4()}_{safe_name}")
        file_storage.save(local_path)
        print("DEBUG saved multipart file:", local_path)
    else:
        # download from URL
        ext = os.path.splitext(_first_url(file_url) or "")[1] or ".pdf"
        local_path = os.path.join(UPLOAD_FOLDER, f"{uuid.uuid4()}{ext}")
        try:
            r = requests.get(file_url, timeout=45)
            r.raise_for_status()
        except Exception as e:
            return jsonify(ok=False, error=f"download error: {e}"), 400
        with open(local_path, "wb") as f:
            f.write(r.content)
        print("DEBUG downloaded URL to:", local_path)

    # ---- 5) Enqueue worker OR do inline placeholder ----
    if q:
        # Example: q.enqueue("worker_tasks.process_layout", local_path, email)
        print("DEBUG enqueued to RQ queue")
        return jsonify(ok=True, queued=True, path=local_path), 202

    # Inline placeholder: generate a BOQ quickly (proves the path)
    df = generate_boq_dataframe(local_path)
    _ = write_boq_xlsx_to_bytes(df)  # bytes ready to email/store
    return jsonify(ok=True, queued=False, path=local_path), 200