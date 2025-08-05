from flask import Flask, request, jsonify
import os
import pandas as pd
from email.message import EmailMessage
import smtplib

app = Flask(__name__)
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

@app.route("/process_layout", methods=["POST"])
def process_layout():
    user_email = request.form.get("email")
    full_name = request.form.get("name")
    uploaded_file = request.files.get("upload_file")

    if not uploaded_file or not user_email:
        return jsonify({"error": "Missing file or email"}), 400

    file_path = os.path.join(UPLOAD_FOLDER, uploaded_file.filename)
    uploaded_file.save(file_path)

    df = pd.DataFrame([
        {"Room": "Living Room", "Area": 32},
        {"Room": "Kitchen", "Area": 12}
    ])
    excel_path = os.path.join(UPLOAD_FOLDER, "BOQ.xlsx")
    df.to_excel(excel_path, index=False)

    msg = EmailMessage()
    msg["Subject"] = "Your BOQ is Ready"
    msg["From"] = "noreply@draftq.ae"
    msg["To"] = user_email
    msg.set_content(f"Hello {full_name},\n\nAttached is your BOQ Excel file.")

    with open(excel_path, "rb") as f:
        msg.add_attachment(f.read(), filename="BOQ.xlsx",
                           maintype="application",
                           subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login("youremail@gmail.com", "your_app_password")
        smtp.send_message(msg)

    return jsonify({"message": "BOQ sent successfully"}), 200