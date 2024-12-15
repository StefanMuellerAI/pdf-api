import os
import tempfile
import base64
import logging

logger = logging.getLogger(__name__)

def encode_page_as_base64(page):
    """Konvertiert eine PDF-Seite in ein base64-kodiertes Bild.
    
    Args:
        page: Eine PyMuPDF-Seite
        
    Returns:
        str: Base64-kodiertes Bild oder None bei Fehler
    """
    try:
        # Konvertiere PDF-Seite zu Bild
        pix = page.get_pixmap()
        
        # Erstelle temporäre Bilddatei
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as temp_img:
            pix.save(temp_img.name)
            
            # Konvertiere zu Base64
            with open(temp_img.name, "rb") as image_file:
                base64_image = base64.b64encode(image_file.read()).decode('utf-8')
            
        # Lösche temporäre Datei
        os.unlink(temp_img.name)
        return base64_image
        
    except Exception as e:
        logger.error(f"Fehler bei der Base64-Kodierung: {e}")
        return None