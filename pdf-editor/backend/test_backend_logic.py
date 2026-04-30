import parser
import fitz
import os

# Create a dummy PDF with PyMuPDF
def create_dummy_pdf(filename):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Hello World", fontsize=20, fontname="helv")
    doc.save(filename)
    return filename

def test_parser():
    filename = "test_dummy.pdf"
    create_dummy_pdf(filename)
    
    with open(filename, "rb") as f:
        data = f.read()
        
    result = parser.parse_pdf(data)
    
    print("Pages:", len(result["pages"]))
    if len(result["pages"]) > 0:
        blocks = result["pages"][0]["blocks"]
        print("Blocks:", len(blocks))
        for b in blocks:
            print(f"Block: {b['content']} - Type: {b['type']} - Bbox: {b['bbox']}")
            if "Hello World" in b["content"]:
                print("SUCCESS: Found text block.")
            else:
                print("FAILURE: Text not found.")

    # Clean up
    if os.path.exists(filename):
        os.remove(filename)

if __name__ == "__main__":
    test_parser()
