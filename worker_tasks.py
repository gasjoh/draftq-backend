import os, io, re, json, uuid, smtplib, requests
import pandas as pd
import numpy as np
import boto3
from email.message import EmailMessage

S3_BUCKET = os.environ["S3_BUCKET"]
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
s3 = boto3.client("s3", region_name=AWS_REGION)

SMTP_HOST = os.environ["SMTP_HOST"]
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.environ["SMTP_USER"]
SMTP_PASS = os.environ["SMTP_PASS"]
SENDER_EMAIL = os.environ["SENDER_EMAIL"]
OCRSPACE_API_KEY = os.environ["OCRSPACE_API_KEY"]

ROOM_WORDS = r"(bed(room)?|living|hall|kitchen|pantry|toilet|bath(room)?|wc|corridor|balcony|store|maid|dining|guest|office|majlis|lobby|study|laundry)"
AREA_WORDS = r"(m2|m²|sqm|sq\.?m|square\s*meters?)"
AREA_REGEX = re.compile(rf"(?P<val>\d+(\.\d+)?)\s*{AREA_WORDS}", re.IGNORECASE)

def s3_get_bytes(key):
    bio = io.BytesIO()
    s3.download_fileobj(S3_BUCKET, key, bio)
    return bio.getvalue()

def s3_put_bytes(key, data, content_type):
    s3.put_object(Bucket=S3_BUCKET, Key=key, Body=data, ContentType=content_type)
    return f"s3://{S3_BUCKET}/{key}"

def s3_signed_url(key, expires=3600):
    return s3.generate_presigned_url(
        "get_object", Params={"Bucket": S3_BUCKET, "Key": key}, ExpiresIn=expires
    )

def send_email(to_email, subject, body, attachments=None):
    msg = EmailMessage()
    msg["From"] = SENDER_EMAIL
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)
    if attachments:
        for att in attachments:
            msg.add_attachment(att["bytes"], maintype=att["maintype"], subtype=att["subtype"], filename=att["filename"])
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)

def ocrspace_parse_bytes(file_bytes, filename):
    url = "https://api.ocr.space/parse/image"
    files = {"file": (filename, file_bytes)}
    data = {
        "apikey": OCRSPACE_API_KEY,
        "language": "eng",
        "isOverlayRequired": "false",
        "isCreateSearchablePdf": "false",
        "isTable": "true",
        "scale": "true",
        "OCREngine": "2"
    }
    resp = requests.post(url, files=files, data=data, timeout=180)
    resp.raise_for_status()
    out = resp.json()
    if not out.get("IsErroredOnProcessing") and out.get("ParsedResults"):
        text = "\n".join([r.get("ParsedText", "") for r in out["ParsedResults"]])
        return text
    raise RuntimeError(f"OCR failed: {json.dumps(out)[:500]}")

def extract_rooms_and_areas(parsed_text):
    """
    Super pragmatic MVP:
    - Split lines, look for room-like tokens near an area token.
    - If no obvious room name near the area token, mark as 'Unknown'.
    """
    lines = [ln.strip() for ln in parsed_text.splitlines() if ln.strip()]
    results = []

    for i, ln in enumerate(lines):
        area_match = AREA_REGEX.search(ln)
        if area_match:
            val = float(area_match.group("val"))
            # search neighborhood for a room word (previous, current, next line)
            nearby = " ".join(lines[max(0, i-1): i+2])
            room_match = re.search(ROOM_WORDS, nearby, re.IGNORECASE)
            room = room_match.group(0).title() if room_match else "Unknown"
            results.append({"room": room, "area_m2": val})

    # dedupe obvious duplicates (sum areas by room label)
    df = pd.DataFrame(results)
    if df.empty:
        return pd.DataFrame(columns=["room", "area_m2"])
    df = df.groupby("room", as_index=False)["area_m2"].sum()
    return df

def estimate_quantities(rooms_df, wall_height_m=3.0):
    """
    Given floor areas, estimate:
      - Floor tiles (for wet areas + corridors by rule)
      - Wall tiles for wet areas (to 2.4 m)
      - Paint area (wall area ≈ perimeter * height; perimeter ≈ 4*sqrt(area) for compact rooms)
    You can refine rules later per your standards.
    """
    if rooms_df.empty:
        return pd.DataFrame(columns=["item", "uom", "qty"])

    def perimeter_from_area(a):  # square-ish assumption
        return 4.0 * (a ** 0.5)

    items = []
    for _, row in rooms_df.iterrows():
        room = row["room"]
        A = float(row["area_m2"])
        P = perimeter_from_area(A)

        # floor tiles:
        if re.search(r"kitchen|toilet|bath|wc|laundry", room, re.IGNORECASE):
            floor_tiles = A * 1.03  # +3% waste
            items.append([f"Floor tiles - {room}", "m²", floor_tiles])
        elif re.search(r"corridor|hall|lobby", room, re.IGNORECASE):
            floor_tiles = A * 1.02
            items.append([f"Floor tiles - {room}", "m²", floor_tiles])

        # wall tiles in wet areas (to 2.4 m), assume 60% of perimeter is tilable walls
        if re.search(r"toilet|bath|wc|laundry|kitchen", room, re.IGNORECASE):
            wall_tiles = (P * 0.6) * 2.4  # m²
            wall_tiles *= 1.05  # +5% waste
            items.append([f"Wall tiles - {room}", "m²", wall_tiles])

        # paint walls (all habitable)
        paint_area = (P * wall_height_m) * 0.9  # 10% deductions for openings/services
        items.append([f"Paint - {room}", "m²", paint_area])

        # skirting (assume along 80% of perimeter), in meters
        skirting = P * 0.8
        items.append([f"Skirting - {room}", "m", skirting])

    df = pd.DataFrame(items, columns=["item", "uom", "qty"])
    # consolidate same item types across rooms if you prefer totals:
    # totals = df.groupby(["item", "uom"], as_index=False)["qty"].sum()
    return df

def make_boq_excel(rooms_df, boq_df, project_name):
    with pd.ExcelWriter(io.BytesIO(), engine="openpyxl") as xw:
        summary = rooms_df.copy()
        summary.rename(columns={"room": "Room", "area_m2": "Area (m²)"}, inplace=True)
        summary.to_excel(xw, index=False, sheet_name="Rooms")

        out = boq_df.copy()
        out["qty"] = out["qty"].round(2)
        out.rename(columns={"item": "Item", "uom": "UoM", "qty": "Quantity"}, inplace=True)
        out.to_excel(xw, index=False, sheet_name="BOQ")

        xw_io = xw._writer._archive.fp  # fetch underlying BytesIO
        xw_io.seek(0)
        return xw_io.read()

def process_layout_job(s3_key, filename, user_email, project_name):
    # 1) Download file
    file_bytes = s3_get_bytes(s3_key)

    # 2) OCR
    parsed_text = ocrspace_parse_bytes(file_bytes, filename)

    # 3) Extract rooms & areas
    rooms_df = extract_rooms_and_areas(parsed_text)

    # 4) Estimate BOQ
    boq_df = estimate_quantities(rooms_df)

    # 5) Save artifacts to S3
    job_id = str(uuid.uuid4())
    # CSV
    csv_bytes = boq_df.to_csv(index=False).encode("utf-8")
    csv_key = f"results/{job_id}/boq.csv"
    s3_put_bytes(csv_key, csv_bytes, "text/csv")
    csv_url = s3_signed_url(csv_key)

    # XLSX
    xlsx_bytes = make_boq_excel(rooms_df, boq_df, project_name)
    xlsx_key = f"results/{job_id}/boq.xlsx"
    s3_put_bytes(xlsx_key, xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    xlsx_url = s3_signed_url(xlsx_key)

    # 6) Email user (links)
    body = (
        f"Project: {project_name}\n\n"
        "Your BOQ is ready.\n"
        f"- CSV: {csv_url}\n"
        f"- Excel: {xlsx_url}\n\n"
        "Note: This is an automatic estimate (MVP). You can reply for refinements."
    )
    send_email(
        to_email=user_email,
        subject=f"[DraftQ] BOQ ready – {project_name}",
        body=body
    )

    # Optional: return result dict (also appears in /job_result)
    return {"csv_url": csv_url, "xlsx_url": xlsx_url, "rooms_found": rooms_df.to_dict(orient="records")}