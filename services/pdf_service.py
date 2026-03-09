import fitz
import io

def split_pdf_pages(pdf_bytes):

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    pages = []

    for page in doc:

        pix = page.get_pixmap()
        img_bytes = pix.tobytes("png")
        
        text_content = page.get_text("text")

        pages.append({
            "image": img_bytes,
            "text": text_content
        })

    return pages