from typing import Tuple, Dict, Any
from config import *
import fitz
import logging
import os
import re
from collections import defaultdict
from thefuzz import fuzz
from mistral import analyze_text_with_mistral, analyze_page_with_pixtral
from ocr import perform_ocr_and_add_text_layer

logger = logging.getLogger(__name__)

REDACTION_FILL_COLOR = tuple(map(int, os.getenv('REDACTION_FILL_COLOR', '0,0,0').split(',')))

def process_single_page(args: Tuple[fitz.Page, int, int, Dict[str, Any]]) -> Tuple[int, fitz.Page]:
    """
    Verarbeitet eine einzelne PDF-Seite.
    
    Args:
        args: Tuple containing (page, page_num, total_pages, preferences)
        
    Returns:
        Tuple containing (page_num, processed_page)
    """
    page, page_num, total_pages, preferences = args
    logger.info(f"Processing page {page_num+1}/{total_pages}")
    
    # Extract text using PyMuPDF
    text = format_page_text(page)
    logger.debug(f"Extracted text from page {page_num+1}")
    
    # Analyze text for sensitive information
    sensitive_data = analyze_text_with_mistral(text, preferences)
    logger.info(f"Found {len(sensitive_data)} potential sensitive items on page {page_num+1}")
    
    # Konsolidiere die Findings
    consolidated_data = consolidate_findings(sensitive_data)
    logger.info(f"Consolidated to {len(consolidated_data)} unique items")
    
    # Validate sensitive data exists in text
    validated_sensitive_data = []
    for item in consolidated_data:
        normalized_text = normalize_text(item['text'])
        normalized_page_text = normalize_text(text)
        
        # Fuzzy Matching für die Validierung
        if any(ratio > 85 for ratio in [
            fuzz.ratio(normalized_text, substr) 
            for substr in [normalized_page_text[i:i+len(normalized_text)] 
                         for i in range(len(normalized_page_text)-len(normalized_text)+1)]
        ]):
            validated_sensitive_data.append(item)
            logger.info(f"Validated sensitive text: '{item['text']}'")
        else:
            logger.warning(f"Ignoring hallucinated text not found in document: '{item['text']}'")
    
    logger.info(f"Validated {len(validated_sensitive_data)} of {len(consolidated_data)} sensitive items on page {page_num+1}")
    
    # Process each validated sensitive item
    applied_redactions = set()  # Verhindere doppelte Schwärzungen
    redactions_added = False  # Flag für Redactions auf dieser Seite
    
    for item in validated_sensitive_data:
        try:
            # Finde alle Vorkommen des Texts
            coords_list = find_text_coordinates_pymupdf(
                page, 
                item['text'],
                is_ocr_text=needs_ocr(page)
            )
            
            if coords_list:
                for coords in coords_list:
                    coord_key = f"{coords[0]:.1f},{coords[1]:.1f},{coords[2]:.1f},{coords[3]:.1f}"
                    
                    if coord_key not in applied_redactions:
                        # Create redaction annotation
                        page.add_redact_annot(
                            fitz.Rect(coords),
                            fill=REDACTION_FILL_COLOR
                        )
                        applied_redactions.add(coord_key)
                        redactions_added = True
                        
                        logger.info(f"Added redactionat {coord_key}")
            else:
            
                logger.warning(f"No valid coordinates found")
                
        except Exception as e:
            logger.error(f"Error processing sensitive item: {str(e)}")
            continue
    
    # Wende alle Redactions auf der Seite an
    if redactions_added:
        try:
            page.apply_redactions()
            logger.info(f"Applied all redactions on page {page_num+1}")
        except Exception as e:
            logger.error(f"Error applying redactions on page {page_num+1}: {str(e)}")
    
    return page_num, page

def format_page_text(page):
    """Formatiert den Text einer PDF-Seite in verschiedenen Formaten."""
    try:
        # Prüfe ob OCR benötigt wird
        if needs_ocr(page):
            logger.info("Seite benötigt OCR - Füge Text-Layer hinzu")
            if perform_ocr_and_add_text_layer(page):
                # Nach dem Hinzufügen des Text-Layers können wir den Text normal extrahieren
                text = page.get_text("text").strip()
            else:
                logger.warning("OCR Text-Layer konnte nicht hinzugefügt werden")
                text = ""
        else:
            text = page.get_text("text").strip()
        
        # Hole Pixtral-Analyse
        pixtral_analysis = analyze_page_with_pixtral(page)
        
        # Kombiniere die Formate
        combined_text = (
            "Dies sind verschiedene Varianten des selben Inhalts, um eine bessere Analyse zu ermöglichen:\n\n"
            "=== EXTRAHIERTER TEXT ===\n"
            f"{text}\n\n"
            "=== PIXTRAL VISION ANALYSE ===\n"
            f"{pixtral_analysis if pixtral_analysis else 'Keine Vision-Analyse verfügbar'}"
        )
        
        return combined_text
        
    except Exception as e:
        logger.error(f"Fehler bei der Textextraktion: {e}")
        return page.get_text("text").strip()  # Fallback zur einfachen Textextraktion
    
def consolidate_findings(findings):
    """
    Konsolidiert und dedupliziert Findings.
    
    Args:
        findings (list): Liste von Findings von Mistral
        
    Returns:
        list: Konsolidierte Liste von Findings
    """
    # Gruppiere Findings nach Typ
    grouped = defaultdict(list)
    for finding in findings:
        grouped[finding['type']].append(finding)
    
    consolidated = []
    for type_id, type_findings in grouped.items():
        # Dedupliziere ähnliche Texte
        unique_findings = []
        seen_texts = set()
        
        for finding in type_findings:
            normalized_text = normalize_text(finding['text'])
            
            # Prüfe ob ein ähnlicher Text bereits existiert
            is_duplicate = False
            for seen_text in seen_texts:
                if fuzz.ratio(normalized_text, seen_text) > 85:
                    is_duplicate = True
                    break
            
            if not is_duplicate:
                seen_texts.add(normalized_text)
                unique_findings.append(finding)
        
        consolidated.extend(unique_findings)
    
    return consolidated

def normalize_text(text):
    """Normalisiert Text für besseres Matching."""
    # Entferne Sonderzeichen und überflüssige Whitespaces
    text = re.sub(r'[^\w\s-]', ' ', text)
    # Normalisiere Whitespaces
    text = ' '.join(text.split())
    # Konvertiere zu Kleinbuchstaben
    return text.lower().strip()


def find_fuzzy_matches(page_text, target_text, min_ratio=85):
    """
    Findet ähnliche Textstellen mit Fuzzy Matching.
    
    Args:
        page_text (str): Der zu durchsuchende Text
        target_text (str): Der zu findende Text
        min_ratio (int): Minimale Ähnlichkeit (0-100)
    
    Returns:
        list: Liste von Fundstellen (Start, Ende, Ähnlichkeit)
    """
    matches = []
    normalized_target = normalize_text(target_text)
    target_len = len(normalized_target.split())
    
    # Teile den Text in Worte
    words = normalize_text(page_text).split()
    
    # Sliding window über den Text
    for i in range(len(words) - target_len + 1):
        window = ' '.join(words[i:i + target_len])
        ratio = fuzz.ratio(window, normalized_target)
        
        if ratio >= min_ratio:
            # Finde die originale Position im Text
            start_pos = page_text.lower().find(words[i])
            end_pos = start_pos + len(' '.join(words[i:i + target_len]))
            matches.append((start_pos, end_pos, ratio))
    
    return matches

def find_text_coordinates_pymupdf(page, target_text, is_ocr_text=False):
    """Finde die Koordinaten des Zieltexts auf einer PDF-Seite."""
    try:
        if is_ocr_text and hasattr(page, 'ocr_data'):
            valid_instances = []
            
            # Hole OCR-Daten
            data = page.ocr_data
            zoom = data['zoom']
            
            # Für Namen: Erstelle eine Liste zusammenhängender Wörter
            words = data['words']
            n_boxes = len(words)
            
            # Suche nach dem Text in den OCR-Ergebnissen
            i = 0
            while i < n_boxes:
                if data['conf'][i] > 60:  # Nur Ergebnisse mit guter Konfidenz
                    # Versuche mehrere aufeinanderfolgende Wörter zu kombinieren
                    combined_text = ""
                    start_idx = i
                    word_count = 0
                    max_words = 5  # Maximale Anzahl von Wörtern, die kombiniert werden
                    
                    while i < n_boxes and data['conf'][i] > 60 and word_count < max_words:
                        word = words[i].strip()
                        if not word:
                            i += 1
                            continue
                            
                        test_text = (combined_text + " " + word).strip()
                        
                        # Prüfe ob der exakte Zieltext im kombinierten Text enthalten ist
                        if target_text.lower() == test_text.lower():
                            # Exakter Match gefunden
                            x0 = data['left'][start_idx] / zoom
                            y0 = data['top'][start_idx] / zoom
                            x1 = (data['left'][i] + data['width'][i]) / zoom
                            y1 = (data['top'][i] + data['height'][i]) / zoom
                            
                            # Prüfe auf Überlappung mit existierenden Rechtecken
                            is_duplicate = False
                            for rect in valid_instances:
                                if (abs(rect[0] - x0) < 20 and abs(rect[1] - y0) < 5):
                                    is_duplicate = True
                                    break
                            
                            if not is_duplicate:
                                valid_instances.append([x0, y0, x1, y1])
                                logger.info(f"Gefunden via OCR: {target_text} bei ({x0:.1f}, {y0:.1f}, {x1:.1f}, {y1:.1f})")
                            break
                        
                        combined_text = test_text
                        word_count += 1
                        i += 1
                        
                        # Wenn der Zieltext als Teil enthalten ist, prüfe genauer
                        if target_text.lower() in test_text.lower():
                            # Finde die genauen Grenzen des Zieltexts
                            start_pos = test_text.lower().find(target_text.lower())
                            end_pos = start_pos + len(target_text)
                            
                            # Berechne die entsprechenden Koordinaten
                            words_before = test_text[:start_pos].count(' ')
                            words_target = target_text.count(' ') + 1
                            
                            x0 = data['left'][start_idx + words_before] / zoom
                            y0 = data['top'][start_idx + words_before] / zoom
                            x1 = (data['left'][start_idx + words_before + words_target - 1] + 
                                 data['width'][start_idx + words_before + words_target - 1]) / zoom
                            y1 = (data['top'][start_idx + words_before + words_target - 1] + 
                                 data['height'][start_idx + words_before + words_target - 1]) / zoom
                            
                            # Prüfe auf Überlappung
                            is_duplicate = False
                            for rect in valid_instances:
                                if (abs(rect[0] - x0) < 20 and abs(rect[1] - y0) < 5):
                                    is_duplicate = True
                                    break
                            
                            if not is_duplicate:
                                valid_instances.append([x0, y0, x1, y1])
                                logger.info(f"Gefunden via OCR (Teil): {target_text} bei ({x0:.1f}, {y0:.1f}, {x1:.1f}, {y1:.1f})")
                            break
                    
                    i = start_idx + 1
                else:
                    i += 1
            
            return [tuple(rect) for rect in valid_instances]
            
        else:
            # Originale Implementierung für normalen Text
            valid_instances = []
            page_width = page.rect.width
            page_height = page.rect.height
            
            text_instances = page.search_for(target_text)
            
            for rect in text_instances:
                x0, y0, x1, y1 = rect
                
                is_valid = (
                    0 <= x0 < page_width and
                    0 <= x1 <= page_width and
                    0 <= y0 < page_height and
                    0 <= y1 <= page_height and
                    x1 - x0 >= 5 and
                    y1 - y0 >= 5 and
                    x1 - x0 < page_width * 0.8 and
                    y1 - y0 < 50
                )
                
                if is_valid:
                    valid_instances.append(rect)
                    logger.info(f"Gefunden: {target_text} bei ({x0:.1f}, {y0:.1f}, {x1:.1f}, {y1:.1f})")
            
            return valid_instances
            
    except Exception as e:
        logger.error(f"Fehler bei der Koordinatenfindung: {e}")
        return []
    
def needs_ocr(page):
    """
    Prüft, ob eine Seite OCR benötigt.
    
    Args:
        page: PyMuPDF-Seite
        
    Returns:
        bool: True wenn OCR benötigt wird
    """
    try:
        # Prüfe auf eingebettete Fonts
        has_embedded_fonts = any(font[3] for font in page.get_fonts())
        
        # Prüfe ob Text extrahierbar ist (mindestens 10 Zeichen)
        extractable_text = len(page.get_text().strip()) > 10
        
        return not (has_embedded_fonts and extractable_text)
        
    except Exception as e:
        logger.error(f"Fehler bei Font-Prüfung: {e}")
        # Im Fehlerfall gehen wir davon aus, dass OCR benötigt wird
        return True
    

    




