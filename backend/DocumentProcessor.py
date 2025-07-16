import pytesseract
from fastapi import HTTPException
from PIL import Image
import io
import os
import logging
from typing import Dict, Any

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DocumentProcessor:
    def __init__(self):
        self.supported_formats = ['.png', '.jpg', '.jpeg', '.pdf', '.tiff', '.bmp']
    
    def extract_text_from_image(self, image_bytes: bytes) -> str:
        """Extract text from image using Tesseract OCR"""
        try:
            image = Image.open(io.BytesIO(image_bytes))
            # Convert to RGB if necessary
            if image.mode != 'RGB':
                image = image.convert('RGB')
            
            # Use Tesseract to extract text
            text = pytesseract.image_to_string(image)
            return text.strip()
        except Exception as e:
            logger.error(f"OCR processing failed: {str(e)}")
            raise HTTPException(status_code=500, detail=f"OCR processing failed: {str(e)}")
    
    def process_document(self, file_content: bytes, filename: str) -> Dict[str, Any]:
        """Process uploaded document and extract text"""
        file_ext = os.path.splitext(filename)[1].lower()
        
        if file_ext not in self.supported_formats:
            raise HTTPException(
                status_code=400, 
                detail=f"Unsupported file format. Supported: {', '.join(self.supported_formats)}"
            )
        
        # For now, we'll handle images directly
        # TODO: Add PDF processing with pdf2image
        if file_ext == '.pdf':
            raise HTTPException(status_code=400, detail="PDF support coming soon")
        
        # Extract text using OCR
        extracted_text = self.extract_text_from_image(file_content)
        
        return {
            "filename": filename,
            "file_size": len(file_content),
            "extracted_text": extracted_text,
            "text_length": len(extracted_text)
        }

# Initialize document processor
doc_processor = DocumentProcessor()