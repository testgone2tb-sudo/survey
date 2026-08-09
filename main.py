import os
import json
import io
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse
from google import genai
from PIL import Image
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = FastAPI(title="Survey OCR Application")

# --- Environment Configuration ---
# API Keys can be pulled from Render Environment Variables or hardcoded as fallbacks
API_KEYS = [
    os.getenv("GEMINI_API_KEY_1", "YOUR_API_KEY_1"),
    os.getenv("GEMINI_API_KEY_2", "YOUR_API_KEY_2"),
    os.getenv("GEMINI_API_KEY_3", "YOUR_API_KEY_3")
]
current_key_index = 0

SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Survey Results")
CREDENTIALS_FILE = "google_sheets_credentials.json"

def get_next_api_client():
    """Rotates through the 3 API keys sequentially."""
    global current_key_index
    key = API_KEYS[current_key_index]
    current_key_index = (current_key_index + 1) % len(API_KEYS)
    return genai.Client(api_key=key)

def write_to_google_sheet(data_dict: dict):
    """Writes the JSON dictionary to Google Sheets."""
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    
    # Check if credentials are passed via environment variable (best for Render)
    cred_json_env = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if cred_json_env:
        creds_dict = json.loads(cred_json_env)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    else:
        creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)

    client = gspread.authorize(creds)
    sheet = client.open(SHEET_NAME).sheet1

    # Define standard column headers
    headers = ["Name"] + [f"Q{i}" for i in range(1, 39)]
    
    # Create row, inserting 'N/A' for missing fields
    row_data = [data_dict.get(col, "N/A") for col in headers]
    sheet.append_row(row_data)

@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    """Serves the frontend interface."""
    if os.path.exists("index.html"):
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>index.html not found.</h1>"

@app.post("/process")
async def process_images(images: list[UploadFile] = File(...)):
    if len(images) != 6:
        raise HTTPException(status_code=400, detail="Exactly 6 images are required.")

    try:
        # Load images into PIL
        pil_images = []
        for file in images:
            content = await file.read()
            img = Image.open(io.BytesIO(content))
            pil_images.append(img)
        
        # Select rotated client key
        client = get_next_api_client()

        prompt = """
        You are an OCR and survey data extraction tool. Analyze these 6 pages of a single questionnaire response.
        Extract the respondent's Name, and the selected answers for Questions Q1 through Q38.
        If a question is left blank, unclear, or missing, output "N/A" for that specific question.
        
        Return ONLY a clean JSON object with this exact structure (no markdown formatting or code blocks):
        {
          "Name": "Extracted Name",
          "Q1": "Answer",
          "Q2": "Answer",
          ...
          "Q38": "Answer"
        }
        """
        
        contents = pil_images + [prompt]
        response = client.models.generate_content(
            model="gemini-3.5-flash",
            contents=contents
        )
        
        # Clean response and parse JSON
        raw_text = response.text.strip().removeprefix("```json").removesuffix("```").strip()
        extracted_data = json.loads(raw_text)
        
        # Append to Google Sheets
        write_to_google_sheet(extracted_data)
        
        return {"status": "success", "data": extracted_data}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
