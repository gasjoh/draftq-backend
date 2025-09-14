import os
import io
import re
import mimetypes
import traceback

import pandas as pd

# OCR deps
from PIL import Image
import pytesseract
import pypdfium2 as pdfium
import requests

# Email
import smtplib
from email.message import EmailMessage

DIM_RE = re.compile(r'(?P<w>\d+(?:\.\d+)?)\s*[x×]\s*(?P<h>\d+(?:\.\d+)?)\s*(m|meter|metre|meters|metres)?', re.I)
OUT_DIR = "/tmp"
os.makedirs(OUT_DIR, exist_ok=True)

def _pdf_to_images(pdf_path):
    """Convert PDF pages to PIL Images using pdfium (works on Render)."""
    images = []
    pdf = pdfium.PdfDocument(pdf_path)
    for i in range(len(pdf)):
        page = pdf[i]
        pil_image = page.render(scale=2).to_pil()
        images.append(pil_image)
    return images

def _ocrspace_image(pil_img, api_key):
    buff = io.BytesIO()
    pil_img.save(buff, format="PNG")
    buff.seek(0)
    resp = requests.post(
        "https://api.ocr.space/parse/image",
        headers={"apikey": api_key},
        files={"file": ("page.png", buff, "image/png")},
        data={"language": "eng"}
    )
    resp.raise_for_status()
    data = resp.json()
    return "\n".join([r.get("ParsedText", "") for r in data.get("ParsedResults", [])])

def ocr_document(path) -> str:
    api_key = os.environ.get("OCRSPACE_API_KEY", "").strip()
    if path.lower().endswith(".pdf"):
        images = _pdf_to_images(path)
    else:
        images = [Image.open(path)]

    chunks = []
    for img in images:
        if api_key:
            chunks.append(_ocrspace_image(img, api_key))
        else:
            chunks.append(pytesseract.image_to_string(img))
    return "\n".join(chunks)

def extract_rooms_and_dims(text: str):
    """Find lines like 'Bedroom 3.20 x 4.00 m'."""
    rooms = []
    for line in text.splitlines():
        m = DIM_RE.search(line)
        if not m:
            continue
        before = line[:m.start()].strip(":-— \t")
        room = before if before else "Room"
        rooms.append({
            "room": room.strip(),
            "w": float(m.group("w")),
            "h": float(m.group("h")),
            "unit": "m",
        })
    return rooms

def build_boq_dataframe(rooms):
    rows = []
    for r in rooms:
        area = r["w"] * r["h"]
        perimeter = 2 * (r["w"] + r["h"])
        rows.append({"Item": f"{r['room']} – Floor area", "Unit": "m²", "Qty": round(area, 2)})
        rows.append({"Item": f"{r['room']} – Perimeter (skirting)", "Unit": "m", "Qty": round(perimeter, 2)})
    return pd.DataFrame(rows)

def save_boq_files(df, base_name="boq"):
    excel_path = os.path.join(OUT_DIR, f"{os.path.splitext(base_name)[0]}_boq.xlsx")
    csv_path   = os.path.join(OUT_DIR, f"{os.path.splitext(base_name)[0]}_boq.csv")
    with pd.ExcelWriter(excel_path) as writer:
        df.to_excel(writer, index=False, sheet_name="BOQ")
    df.to_csv(csv_path, index=False)
    return {"excel_path": excel_path, "csv_path": csv_path}

def send_boq_email(to_email, subject, body, attachments=None):
    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", "465"))
    user = os.environ.get("SMTP_USER")
    pwd  = os.environ.get("SMTP_PASS")
    sender = os.environ.get("SENDER_EMAIL", user)

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    for path in attachments or []:
        ctype, _ = mimetypes.guess_type(path)
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        with open(path, "rb") as f:
            msg.add_attachment(f.read(), maintype=maintype, subtype=subtype, filename=os.path.basename(path))

    with smtplib.SMTP_SSL(host, port) as s:
        s.login(user, pwd)
        s.send_message(msg)

def process_layout_job(file_path: str, user_email: str):
    """
    Queue job:
    1) OCR -> text
    2) Parse room dims
    3) Build BOQ
    4) Save Excel/CSV
    5) Email to user
    """
    try:
        text  = ocr_document(file_path)
        rooms = extract_rooms_and_dims(text)
        df    = build_boq_dataframe(rooms)
        outputs = save_boq_files(df, base_name=os.path.basename(file_path))

        send_boq_email(
            to_email=user_email,
            subject="Your DraftQ BOQ",
            body="Attached is the BOQ generated from your layout (MVP).",
            attachments=[outputs["excel_path"]]
        )
        return {"ok": True, "results": outputs}
    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "error": str(e)}