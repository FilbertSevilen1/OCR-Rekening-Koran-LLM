import fitz
import io

def split_pdf_pages(pdf_bytes):

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    pages = []

    for page in doc:
        # Higher resolution for better OCR: scale=2.0 (approx 144 DPI) or scale=4.0 (+- 300 DPI)
        # Using 300 DPI (approx scale=4) for pixel-perfect numbers
        matrix = fitz.Matrix(4, 4)
        pix = page.get_pixmap(matrix=matrix)
        img_bytes = pix.tobytes("png")
        
        pages.append({
            "image": img_bytes
        })

    return pages