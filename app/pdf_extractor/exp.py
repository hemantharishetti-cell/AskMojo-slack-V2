import fitz  # PyMuPDF
from PIL import Image
import os


def pdf_to_images(pdf_path: str, output_dir: str = None, dpi: int = 300):
    """
    Convert all pages of a PDF into images.

    Args:
        pdf_path (str): Path to input PDF.
        output_dir (str): Directory to save images (optional).
        dpi (int): Rendering resolution (recommended 200-300).

    Returns:
        List of PIL Image objects OR saved file paths (if output_dir provided).
    """
    
    doc = fitz.open(pdf_path)
    images = []

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    for page_number in range(len(doc)):
        page = doc[page_number]
        pix = page.get_pixmap(dpi=dpi)

        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

        if output_dir:
            img_path = os.path.join(output_dir, f"page_{page_number+1}.png")
            img.save(img_path)
            images.append(img_path)
        else:
            images.append(img)

    doc.close()
    return images

from paddleocr import PaddleOCR
import numpy as np


def ocr_images(image_list):
    """
    Perform OCR using PaddleOCR 2.7.x (stable)
    """

    # OLD API (correct for 2.7)
    ocr = PaddleOCR(use_angle_cls=True, lang='en')

    results = []

    for idx, img in enumerate(image_list):

        if not isinstance(img, str):
            img = np.array(img)

        ocr_result = ocr.ocr(img, cls=True)

        extracted_text = []

        for line in ocr_result:
            for word_info in line:
                extracted_text.append(word_info[1][0])

        page_text = "\n".join(extracted_text)

        results.append({
            "page_number": idx + 1,
            "text": page_text
        })

    return results

if __name__ == "__main__":
    images = pdf_to_images("test.pdf")
    results = ocr_images(images)  
    for result in results:
        print("Page", result["page_number"], ":", result["text"], "\n")    