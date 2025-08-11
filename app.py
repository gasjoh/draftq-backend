import os
from flask import Flask, request, jsonify
from flask_cors import CORS
import pandas as pd

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

    # Save uploaded file
    file_path = os.path.join(UPLOAD_FOLDER, uploaded_file.filename)
    uploaded_file.save(file_path)

    # Dummy DataFrame (replace with your real parsing later)
    df = pd.DataFrame([
        {"Room": "Living Room", "Area": 32},
        {"Room": "Kitchen", "Area": 12},
    ])

    # Save BOQ to /tmp
    excel_path = os.path.join(UPLOAD_FOLDER, "BOQ.xlsx")
    df.to_excel(excel_path, index=False)

    # TEMP: bypass email to test endpoint
    return jsonify(message="BOQ generated (email disabled)"), 200