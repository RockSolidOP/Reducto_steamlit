import os
import json
import base64
import re
from io import BytesIO
import fitz
from PIL import Image
import pytesseract
from pathlib import Path

# Write all outputs under a local "output" folder in this directory
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def detect_checkbox_state(doc, page_num, rect_coords):
    try:
        page = doc.load_page(page_num - 1)
        scale = 5
        x0, y0, x1, y1 = rect_coords
        rect_to_render = fitz.Rect(x0, y0, x1, y1)
        pix = page.get_pixmap(clip=rect_to_render, matrix=fitz.Matrix(scale, scale))

        img_data = pix.tobytes("ppm")
        img = Image.open(BytesIO(img_data)).convert('L')

        width, height = img.size
        check_box = (
            int(width * 0.2),
            int(height * 0.2),
            int(width * 0.8),
            int(height * 0.8)
        )
        
        central_pixels = img.crop(check_box).getdata()
        dark_pixel_count = sum(1 for pixel in central_pixels if pixel < 100)
        total_pixels = len(central_pixels)
        dark_pixel_ratio = dark_pixel_count / total_pixels
        return "Checked" if dark_pixel_ratio > 0.1 else "Unchecked"
    except Exception as e:
        print(f"Checkbox detection failed: {e}")
        return "Error"

def extract_text_pymupdf(doc, page_num, rect_coords):
    try:
        page = doc.load_page(page_num - 1)
        rect = fitz.Rect(rect_coords)
        text = page.get_text(clip=rect, sort=True).strip()
        return text if text else None
    except Exception as e:
        print(f"PyMuPDF text extraction failed: {e}")
        return None

def extract_text_tesseract(doc, page_num, rect_coords):
    try:
        page = doc.load_page(page_num - 1)
        pix = page.get_pixmap(matrix=fitz.Matrix(3, 3)) 
        img_data = pix.tobytes("ppm")
        img = Image.open(BytesIO(img_data))
        scale = 3
        x0, y0, x1, y1 = [int(c * scale) for c in rect_coords]
        cropped_img = img.crop((x0, y0, x1, y1))
        text = pytesseract.image_to_string(cropped_img).strip()
        return text if text else None
    except Exception as e:
        print(f"Tesseract OCR failed: {e}")
        return None

def extract_data_with_template(template_file: str, form_fields_file: str, filename: str, pdf_path: Path):
    extracted_data = {}

    if not os.path.exists(pdf_path):
        print(f"Input pdf file does not exist: {pdf_path}")
        return 
    
    try:
        with open(template_file, 'r') as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"Error: Template file not found - {template_file}")
        return
    except json.JSONDecodeError:
        print(f"Error: {template_file} is invalid JSON")
        return 
    try:
        doc = fitz.open(pdf_path)
        print(">>> We got doc")
        for field in data:
            label = field.get("label")
            rect_coords = field.get('rect') 
            page_num = field.get('page')
            field_type = field.get('type', 'text')

            value = None
            if field_type == 'checkbox':
                value = detect_checkbox_state(doc, page_num, rect_coords)
                extracted_data[label] = value
                continue

            value = extract_text_pymupdf(doc, page_num, rect_coords)
            if not value:
                print(f"PyMuPDF failed for '{label}'. Falling back to Tesseract OCR...")
                value = extract_text_tesseract(doc, page_num, rect_coords)
            extracted_data[label] = value if value else "N/A"
        doc.close()


        base_name = filename.split("/")[-1]
        print(f"Base file: {base_name}")
        print(f"==== Full name: {filename} and File base: {base_name}")
        extraction_filename = f"{base_name}_extract.json"
        extraction_path = OUTPUT_DIR / extraction_filename
        with open(extraction_path, 'w') as f:
            json.dump(extracted_data, f, indent=4)
        return
    except Exception as e:
        print(f"Extraction process failed: {e}")
        return

if __name__ == "__main__":
    # Use local files within this LocalTemplateCode folder
    local_template = BASE_DIR / "A7SDEPR3_Form 1040 Individual_template.json"
    local_pdf = BASE_DIR / "A7SDEPR3.pdf"
    extract_data_with_template(
        template_file=str(local_template),
        form_fields_file=str(BASE_DIR / "form_fields.json"),
        filename=str(local_pdf.name),
        pdf_path=local_pdf,
    )
