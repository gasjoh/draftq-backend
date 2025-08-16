import os
import mimetypes
from flask import Flask, request, jsonify
from flask_cors import CORS
from email.message import EmailMessage
import smtplib
from redis import Redis
from rq import Queue
from worker_tasks import process_layout_task  # import the function you want the worker to run

# connect to Redis (using the REDIS_URL environment variable we set in Render)
redis_conn = Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"))

# create a queue (we’ll just use the default queue)
q = Queue("default", connection=redis_conn)
from werkzeug.utils import secure_filename
from uuid import uuid4
import boto3

app = Flask(__name__)
CORS(app)  # allow your frontend to call the API
UPLOAD_FOLDER = "/tmp"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
s3 = boto3.client("s3", region_name=os.getenv("AWS_REGION"))
def upload_to_s3(local_path, bucket, key):
    s3.upload_file(local_path, bucket, key)
    return f"s3://{bucket}/{key}"

# Use ephemeral disk on Render
UPLOAD_FOLDER = "/tmp"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ------------- Email config (from environment variables) -------------
def _get_bool(name, default="false"):
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "y", "on")

SMTP_HOST = os.getenv("SMTP_HOST", "")          # e.g. smtp.gmail.com or smtp.office365.com
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))  # 587 (TLS) or 465 (SSL)
SMTP_USER = os.getenv("SMTP_USER", "")          # full username (email)
SMTP_PASS = os.getenv("SMTP_PASS", "")          # app password or SMTP password
SMTP_USE_SSL = _get_bool("SMTP_USE_SSL", "false")
SMTP_USE_STARTTLS = _get_bool("SMTP_USE_STARTTLS", "true")  # usually True for 587

FROM_EMAIL = os.getenv("FROM_EMAIL", SMTP_USER)
FROM_NAME = os.getenv("FROM_NAME", "DraftQ")

def send_email_smtp(to_email: str, subject: str, body_text: str, attachments=None):
    """
    Send an email via SMTP with optional attachments.
    attachments: list of file paths
    """
    if not (SMTP_HOST and SMTP_PORT and SMTP_USER and SMTP_PASS):
        raise RuntimeError("SMTP is not configured: set SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASS")

    msg = EmailMessage()
    msg["From"] = f"{FROM_NAME} <{FROM_EMAIL or SMTP_USER}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body_text)

    attachments = attachments or []
    for path in attachments:
        if not path or not os.path.isfile(path):
            continue
        ctype, encoding = mimetypes.guess_type(path)
        if ctype is None:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        with open(path, "rb") as f:
            msg.add_attachment(f.read(), maintype=maintype, subtype=subtype, filename=os.path.basename(path))

    if SMTP_USE_SSL:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
    else:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            if SMTP_USE_STARTTLS:
                server.starttls()
                server.ehlo()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)

# ------------------------- Routes -------------------------

@app.route("/", methods=["GET"])
def home():
    return jsonify(message="DraftQ backend is running")

@app.route("/health", methods=["GET"])
def health():
    return jsonify(ok=True)

@app.route("/process_layout", methods=["POST"])
def process_layout():
    try:
        file = request.files.get("file")
        user_email = request.form.get("email", "").strip()

        if not file or not user_email:
            return jsonify(ok=False, error="Missing file or email"), 400

        # save upload to /tmp (Render's ephemeral disk)
        filename = secure_filename(file.filename or f"layout_{uuid4().hex}.pdf")
        local_path = os.path.join(UPLOAD_FOLDER, filename)
        file.save(local_path)

        # upload to S3
        bucket = os.environ["S3_BUCKET"]
        s3_key = f"uploads/{uuid4().hex}_{filename}"
        upload_to_s3(local_path, bucket, s3_key)

        # enqueue background processing (worker will pull from S3)
        job = q.enqueue(process_layout_task, s3_key, user_email)

        return jsonify(ok=True, job_id=job.id, message="Job enqueued")
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500
        # Fall back to previous behavior (no email) but return the reason
        return jsonify(ok=False, message="BOQ generated (email failed)", email_sent=False, error=str(e)), 500

@app.route("/send_test_email", methods=["POST"])
def send_test_email():
    """
    Simple test endpoint: POST JSON {"to":"you@example.com"} to verify SMTP env.
    """
    data = request.get_json(force=True)
    to_email = data.get("to")
    if not to_email:
        return jsonify(ok=False, error="Missing 'to'"), 400

    try:
        send_email_smtp(
            to_email=to_email,
            subject="DraftQ test email",
            body_text="This is a test email from DraftQ backend. 🎯"
        )
        return jsonify(ok=True, message=f"Test email sent to {to_email}")
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))

CORS(app)

@app.route("/", methods=["GET"])
def home():
    return jsonify(ok=True, message="DraftQ web is running")

@app.route("/health", methods=["GET"])
def health():
    return jsonify(ok=True)

def s3_put_bytes(key, data, content_type):
    s3.put_object(Bucket=S3_BUCKET, Key=key, Body=data, ContentType=content_type)
    return f"s3://{S3_BUCKET}/{key}"

def s3_signed_url(key, expires=3600):
    return s3.generate_presigned_url(
        "get_object", Params={"Bucket": S3_BUCKET, "Key": key}, ExpiresIn=expires
    )

@app.route("/process_layout", methods=["POST"])
def process_layout():
    """
    Form fields expected:
      - file: uploaded plan (PDF or image)
      - user_email: target email for BOQ
      - project_name (optional)
    """
    file = request.files.get("file")
    user_email = request.form.get("user_email")
    project_name = request.form.get("project_name", "Untitled Project")

    if not file or not user_email:
        return jsonify(ok=False, error="file and user_email are required"), 400

    ext = os.path.splitext(file.filename.lower())[1] or ".pdf"
    content_type = file.mimetype or ("application/pdf" if ext == ".pdf" else "application/octet-stream")

    upload_id = str(uuid.uuid4())
    s3_key = f"uploads/{upload_id}{ext}"
    s3_uri = s3_put_bytes(s3_key, file.read(), content_type)

    # Enqueue background job
    from worker_tasks import process_layout_job   # imported here to avoid circular import
    job = q.enqueue(
        process_layout_job,
        s3_key=s3_key,
        filename=file.filename,
        user_email=user_email,
        project_name=project_name
    )

    return jsonify(ok=True, job_id=job.get_id())

@app.route("/job_status/<job_id>", methods=["GET"])
def job_status(job_id):
    from rq.job import Job
    try:
        job = Job.fetch(job_id, connection=redis_conn)
    except Exception:
        return jsonify(ok=False, status="not_found"), 404

    status = job.get_status()
    meta = job.meta or {}
    return jsonify(ok=True, status=status, meta=meta)

@app.route("/job_result/<job_id>", methods=["GET"])
def job_result(job_id):
    from rq.job import Job
    try:
        job = Job.fetch(job_id, connection=redis_conn)
    except Exception:
        return jsonify(ok=False, status="not_found"), 404

    if job.is_finished:
        return jsonify(ok=True, result=job.result)
    elif job.is_failed:
        return jsonify(ok=False, status="failed", error=str(job.exc_info))
    else:
        return jsonify(ok=False, status=job.get_status())

def upload_to_s3(local_path, bucket, key):
    s3.upload_file(local_path, bucket, key)
    return f"s3://{bucket}/{key}"
