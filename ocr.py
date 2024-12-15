import os
import tempfile
import pytesseract
from PIL import Image
import fitz
import logging

logger = logging.getLogger(__name__)

def perform_ocr_and_add_text_layer(page):
    """
    Führt OCR durch und fügt den erkannten Text als durchsuchbare Ebene ein.
    
    Args:
        page: PyMuPDF-Seite
        
    Returns:
        dict: Dictionary mit OCR-Text und Koordinaten oder None bei Fehler
    """
    try:
        # Konvertiere Seite zu Bild mit höherer Auflösung
        zoom = 2
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        
        # Speichere temporär als PNG
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp_file:
            pix.save(tmp_file.name)
            
            # OCR mit Tesseract für Text und Koordinaten
            data = pytesseract.image_to_data(
                Image.open(tmp_file.name),
                lang='deu+eng',
                config='--psm 1',
                output_type=pytesseract.Output.DICT
            )
        
        # Lösche temporäre Datei
        os.unlink(tmp_file.name)
        
        # Speichere OCR-Ergebnisse für spätere Koordinatensuche
        page.ocr_data = {
            'words': data['text'],
            'conf': data['conf'],
            'left': data['left'],
            'top': data['top'],
            'width': data['width'],
            'height': data['height'],
            'zoom': zoom
        }
        
        logger.info("OCR erfolgreich durchgeführt und Daten gespeichert")
        return True
        
    except Exception as e:
        logger.error(f"Fehler bei OCR: {e}")
        return False

