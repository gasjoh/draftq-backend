import os
from flask import Flask, request, jsonify
from flask_cors import CORS
import pandas as pd
from email.message import EmailMessage
import smtplib

app = Flask(__name__)
CORS(app)  # allow your frontend to call the API

# Use ephemeral disk on Render
UPLOAD_FOLDER = "/tmp"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

@app.route("/", methods=["GET"])
def home():
    return jsonify(message="DraftQ backend is running")

@app.route("/health", methods=["GET"])
def health():
    return jsonify(ok=True)

@app.route("/process_layout", methods=["POST"])
def process_layout():
    user_email = request.form.get("email")
    full_name = request.form.get("name")
    uploaded_file = request.files.get("upload_file")

    if not uploaded_file or not user_email:
        return jsonify(error="Missing file or email"), 400

    # Save uploaded file (optional – if you need to keep it briefly)
    file_path = os.path.join(UPLOAD_FOLDER, uploaded_file.filename)
    uploaded_file.save(file_path)

    # ---- Dummy DataFrame (replace with your real parsing later) ----
    df = pd.DataFrame([
        {"Room": "Living Room", "Area": 32},
        {"Room": "Kitchen", "Area": 12},
    ])

    # Save BOQ to /tmp
    excel_path = os.path.join(UPLOAD_FOLDER, "BOQ.xlsx")
    df.to_excel(excel_path, index=False)

    # ---- Send email with attachment (reads creds from env) ----
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")       # your email (or SMTP username)
    smtp_pass = os.getenv("SMTP_PASS")       # app password / SMTP password
    from_email = os.getenv("FROM_EMAIL", smtp_user)

    try:
        msg = EmailMessage()
        msg["Subject"] = "Your BOQ is Ready"
        msg["From"] = from_email
        msg["To"] = user_email
        msg.set_content(f"Hello {full_name},\n\nAttached is your BOQ Excel file.")

        with open(excel_path, "rb") as f:
            msg.add_attachment(
                f.read(),
                maintype="application",
                subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                filename="BOQ.xlsx",
            )

        with smtplib.SMTP(smtp_host, smtp_port) as smtp:
            smtp.starttls()
            smtp.login(smtp_user, smtp_pass)
            smtp.send_message(msg)

        return jsonify(message="BOQ generated and emailed successfully")

    except Exception as e:
        # Return the error to help debugging (remove in production)
        return jsonify(error=str(e)), 500