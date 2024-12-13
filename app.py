from config import *
import os
import tempfile
import io
from datetime import datetime
import logging
from flask import Flask, request, send_file, jsonify
from flask_cors import CORS
from celery import Celery, signals  # Importiere signals Modul
import fitz  # PyMuPDF
from mistralai import Mistral
import json
from thefuzz import fuzz
from collections import defaultdict
import re
import pickle
from datetime import datetime, timedelta
import hashlib
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import requests.exceptions
import smtplib
from email.message import EmailMessage
import base64
import pytesseract
from PIL import Image
import concurrent.futures
from typing import List, Dict, Any, Tuple

# Configure logging
logging.basicConfig(
    level=LOG_LEVEL,
    format=LOG_FORMAT
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
CORS(app)

def notify_admin(subject, message):
    """Sendet eine E-Mail-Benachrichtigung an den Admin."""
    if not all([ADMIN_EMAIL, SMTP_HOST, SMTP_USER, SMTP_PASSWORD]):
        logger.warning("Admin notification configuration incomplete")
        return

    try:
        msg = EmailMessage()
        msg.set_content(message)
        msg['Subject'] = f'PDF-API Alert: {subject}'
        msg['From'] = SMTP_USER
        msg['To'] = ADMIN_EMAIL

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
            logger.info(f"Admin notification sent: {subject}")
    except Exception as e:
        logger.error(f"Failed to send admin notification: {e}")

# Dekoratore für Retry-Logik
@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=INITIAL_WAIT, max=MAX_WAIT),
    retry=retry_if_exception_type((
        RuntimeError,
        ValueError,
        requests.exceptions.RequestException,
        TimeoutError
    ))
)
def call_mistral_with_retry(messages, model):
    """Ruft die Mistral-API mit Retry-Mechanismus auf."""
    try:
        return mistral_client.chat.complete(
            model=model,
            messages=messages,
            response_format={"type": "json_object"}, 
            temperature=0.1
        )
    except Exception as e:
        logger.error(f"Mistral API error after {MAX_RETRIES} retries: {e}")
        notify_admin(
            "Mistral API Issues",
            f"Multiple failed attempts to call Mistral API: {str(e)}"
        )
        raise

def mask_sensitive_text(text, mask=False):
    """
    Maskiert sensiblen Text für Logging-Zwecke.
    
    Args:
        text (str): Zu maskierender Text
        mask (bool): Ob der Text maskiert werden soll
    
    Returns:
        str: Original Text oder maskierter Text
    """
    if not mask:
        return text
        
    if not text:
        return "***[empty]***"
    
    # Erstelle einen Hash des Textes (nur die ersten 6 Zeichen)
    text_hash = hashlib.sha256(text.encode()).hexdigest()[:6]
    
    # Basis-Maskierung
    masked = f"***[hash:{text_hash}]***[len:{len(text)}]"
    
    return masked

def create_minimum_options():
    """Erstellt minimale Default-Optionen."""
    options = {}
    for option_id, is_default in DEFAULT_MINIMUM_OPTIONS.items():
        options[option_id] = {
            'id': option_id,
            'label': option_id.replace('_', ' ').title(),
            'description': f"Detect and redact {option_id.replace('_', ' ')}",
            'default': is_default
        }
    return options

# Load options at startup
try:
    ANONYMIZATION_OPTIONS = create_minimum_options()
except Exception as e:
    logger.error("Failed to load anonymization options, using empty dict")
    ANONYMIZATION_OPTIONS = {}

REDIS_PASSWORD = os.getenv('REDIS_PASSWORD', '')
REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
REDIS_PORT = os.getenv('REDIS_PORT', '6379')
REDIS_DB = os.getenv('REDIS_DB', '0')

REDIS_URL = f"redis://{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"

# Configure Celery
celery = Celery(
    'app',
    broker=REDIS_URL,
    backend=REDIS_URL
)

# Configure Celery for better macOS compatibility and queue purging
celery.conf.update(
    broker_transport_options={
        'visibility_timeout': REDIS_TIMEOUT,
        'fanout_prefix': True,  # Ermöglicht besseres Queue-Management
        'fanout_patterns': True
    },
    redis_socket_timeout=REDIS_TIMEOUT,
    redis_socket_connect_timeout=REDIS_TIMEOUT,
    broker_connection_retry=True,
    broker_connection_max_retries=MAX_RETRIES,
    worker_max_tasks_per_child=1,
    worker_prefetch_multiplier=1,
    worker_pool='solo',
    task_serializer='pickle',
    accept_content=['pickle', 'json'],
    result_serializer='pickle',
    task_default_queue='pdf_tasks',
    task_routes={
        'app.process_pdf': {'queue': 'pdf_tasks'}
    },
    # Queue-Bereinigung beim Start
    worker_reset_tasks_at_start=True,  # Löscht Tasks beim Worker-Start
    task_reject_on_worker_lost=True,   # Verhindert, dass verlorene Tasks wieder aufgenommen werden
    task_acks_late=True                # Tasks werden erst nach erfolgreicher Ausführung bestätigt
)

# Funktion zum Leeren der Queue beim Worker-Start
@celery.task(name='app.purge_queue')
def purge_queue():
    """Leert die Celery Queue beim Worker-Start."""
    try:
        celery.control.purge()
        logger.info("Successfully purged Celery queue")
    except Exception as e:
        logger.error(f"Error purging Celery queue: {e}")

# Worker Signals für Queue-Bereinigung
@signals.worker_ready.connect
def clean_at_start(sender=None, conf=None, **kwargs):
    """Wird ausgeführt, wenn der Worker startet."""
    purge_queue.delay()

# Initialize Mistral client with configuration from environment
mistral_client = Mistral(api_key=os.environ.get('MISTRAL_API_KEY'))
MISTRAL_MODEL = os.getenv('MISTRAL_MODEL', 'mistral-large-latest')

# Get redaction color from environment
REDACTION_FILL_COLOR = tuple(map(int, os.getenv('REDACTION_FILL_COLOR', '0,0,0').split(',')))

FINDING_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["text", "type", "start_index", "confidence"],
                "properties": {
                    "text": {"type": "string", "minLength": 1},
                    "type": {"type": "string"},
                    "start_index": {"type": "integer", "minimum": 0},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1}
                }
            }
        }
    }
}

@app.route('/api/anonymization-options', methods=['GET'])
def get_anonymization_options():
    """Return available anonymization options."""
    return jsonify({
        "options": ANONYMIZATION_OPTIONS
    })

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
                        
                        masked_text = mask_sensitive_text(item['text'])
                        logger.info(f"Added redaction for {masked_text} at {coord_key}")
            else:
                masked_text = mask_sensitive_text(item['text'])
                logger.warning(f"No valid coordinates found for: {masked_text}")
                
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

@celery.task(name='app.process_pdf', bind=True)
def process_pdf(self, pdf_data, preferences):
    """Process PDF and anonymize sensitive information."""
    task_id = self.request.id
    logger.info(f"Starting PDF processing task {task_id}")
    
    try:
        # Save the uploaded PDF temporarily
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as temp_input:
            temp_input.write(pdf_data)
            input_path = temp_input.name
            logger.info(f"Saved temporary input file: {input_path}")

        # Create a BytesIO object to store the processed PDF
        output_buffer = io.BytesIO()

        # Open the PDF with PyMuPDF
        doc = fitz.open(input_path)
        total_pages = len(doc)
        
        # Check for embedded fonts
        has_embedded_fonts = any(font[3] for font in doc.get_page_fonts(0))
        logger.info(f"PDF contains embedded fonts: {has_embedded_fonts}")
        
        # Erstelle eine Liste von Argumenten für die parallele Verarbeitung
        page_args = [(doc[i], i, total_pages, preferences) for i in range(total_pages)]
        
        # Verarbeite die Seiten parallel
        processed_pages = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(os.cpu_count(), total_pages)) as executor:
            # Starte die parallele Verarbeitung
            future_to_page = {executor.submit(process_single_page, args): args[1] for args in page_args}
            
            # Sammle die Ergebnisse und aktualisiere den Fortschritt
            completed_pages = 0
            for future in concurrent.futures.as_completed(future_to_page):
                completed_pages += 1
                self.update_state(state='PROGRESS',
                                meta={'current_page': completed_pages,
                                     'total_pages': total_pages})
                
                try:
                    page_num, processed_page = future.result()
                    processed_pages[page_num] = processed_page
                except Exception as e:
                    logger.error(f"Error processing page {future_to_page[future]}: {str(e)}")
        
        # Save the redacted PDF
        doc.save(output_buffer)
        doc.close()
        
        # Clean up temporary input file
        os.unlink(input_path)
        
        # Get the PDF data from the buffer
        pdf_bytes = output_buffer.getvalue()
        
        logger.info(f"PDF processing completed successfully for task {task_id}")
        return {
            "status": "Completed",
            "message": "PDF processed successfully",
            "pdf_data": pdf_bytes,
            "total_pages": total_pages
        }
    
    except Exception as e:
        logger.error(f"Error processing PDF for task {task_id}: {str(e)}")
        logger.exception("Full traceback:")
        return {
            "status": "Failed",
            "message": str(e)
        }

# Füge diese Konstanten nach den anderen Konstanten hinzu
DEFAULT_SYSTEM_PROMPT = """Als KI-Assistent für Dokumentenanalyse ist es meine Aufgabe, sensible Informationen in Texten zu identifizieren.

Für jede gefundene sensible Information gebe ich zurück:
- Den exakten Text
- Den Typ der Information
- Die Position (Start-Index) im Text
- Eine Konfidenz-Bewertung (0-1)

Ich antworte ausschließlich im JSON-Format mit einem "findings" Array:
{
    "findings": [
        {
            "text": "gefundener Text",
            "type": "typ_id",
            "start_index": position,
            "confidence": konfidenz
        }
    ]
}

Ich achte besonders auf:
- Exakte Textgrenzen ohne zusätzliche Whitespaces
- Korrekte Start-Indizes
- Realistische Konfidenzwerte
- Vermeidung von Falsch-Positiven"""

# Füge diese Funktion nach den Konstanten hinzu
def load_system_prompt():
    """
    Lädt den System-Prompt aus verschiedenen Quellen.
    
    Priorisierung:
    1. Umgebungsvariable SYSTEM_PROMPT
    2. prompt.md Datei
    3. DEFAULT_SYSTEM_PROMPT
    """
    # Prüfe zuerst Umgebungsvariable
    env_prompt = os.getenv('SYSTEM_PROMPT')
    if env_prompt:
        logger.info("Using system prompt from environment variable")
        return env_prompt.strip()
    
    # Versuche prompt.md zu laden
    try:
        prompt_path = os.getenv('PROMPT_PATH', 'prompt.md')
        with open(prompt_path, 'r') as f:
            prompt = f.read().strip()
            logger.info(f"Loaded system prompt from {prompt_path}")
            return prompt
    except Exception as e:
        logger.warning(f"Could not load prompt from {prompt_path}: {e}")
        logger.info("Using default system prompt")
        return DEFAULT_SYSTEM_PROMPT

def sanitize_for_json(obj):
    """Konvertiert ein Objekt in ein JSON-serialisierbares Format."""
    if isinstance(obj, bytes):
        try:
            return obj.decode('utf-8')
        except UnicodeDecodeError:
            return str(obj)
    elif isinstance(obj, dict):
        return {key: sanitize_for_json(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_for_json(item) for item in obj]
    elif isinstance(obj, (int, float, str, bool, type(None))):
        return obj
    else:
        return str(obj)

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

def analyze_page_with_pixtral(page):
    """Analysiert eine PDF-Seite mit dem Pixtral Vision-Modell."""
    try:
        # Kodiere Seite als Base64
        base64_image = encode_page_as_base64(page)
        if not base64_image:
            return None
            
        # Erstelle Pixtral-Anfrage
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Change this image to a json object."
                    },
                    {
                        "type": "image_url",
                        "image_url": f"data:image/png;base64,{base64_image}"
                    }
                ]
            }
        ]
        
        # Rufe Pixtral API auf
        chat_response = mistral_client.chat.complete(
            model=os.getenv('MISTRAL_VISION_MODEL'),
            messages=messages
        )
        
        return chat_response.choices[0].message.content
        
    except Exception as e:
        logger.error(f"Fehler bei der Pixtral-Analyse: {e}")
        return None

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

def analyze_text_with_mistral(text, preferences):
    """Analyze text using Mistral API with improved error handling."""
    logger.info("Analyzing text with Mistral API")
    
    try:
        prompt_parts = []
        # Erstelle dynamischen Prompt basierend auf den Nutzereinstellungen
        enabled_types = [
            option_id for option_id, is_enabled in preferences.items() 
            if is_enabled and is_enabled is True
        ]
        
        if not enabled_types:
            logger.info("Keine Anonymisierungsoptionen aktiviert")
            return []
            
        logger.info("Aktivierte Anonymisierungsoptionen:")
        for type_id in enabled_types:
            logger.info(f"  - {type_id}")
        
        # Erstelle die Typenliste für den Prompt - NUR für aktivierte Typen
        type_descriptions = {
            'addresses': "'addresses' für vollständige Postadressen (NICHT einzelne Straßennamen oder Städte, sondern nur komplette Adressen mit mindestens Straße, Hausnummer und PLZ/Ort)",
            'dates': "'dates' für Datumswerte in verschiedenen Formaten (z.B., '01.01.2024', '2024-01-01', '1. Januar 2024'). NICHT markieren: Uhrzeiten ohne Datum, Jahreszahlen allein",
            'emails': "'emails' für gültige E-Mail-Adressen mit @ und Domain (z.B., 'beispiel@domain.de'). NICHT markieren: Texte mit '@' die keine gültigen E-Mail-Adressen sind",
            'ids': "'ids' für eindeutige Identifikationsnummern (nur Steuer-IDs, Sozialversicherungsnummern, Handelsregisternummern, Ausweisnummern). NICHT markieren: Bestellnummern, Artikelnummern, Rechnungsnummern",
            'names': "'names' ausschließlich für echte Personennamen von Menschen (z.B., 'Dr. Max Mustermann, Stefan Müller; Müller, Stefan; Stefan M.; S. Meier'). NICHT markieren: Firmennamen, Produktnamen, Straßennamen, Städtenamen, Gebäudenamen oder andere Eigennamen",
            'phone_numbers': "'phone_numbers' für Telefon- und Faxnummern mit Vorwahl (z.B., '+49 30 12345678; 0177-5228242;0177/5228242;01775228242'). NICHT markieren: andere Zahlenkombinationen oder Nummern ohne Vorwahl"
        }
        
        # Filtere die Beschreibungen - NUR für aktivierte Typen
        enabled_type_descriptions = {
            t: type_descriptions[t] 
            for t in enabled_types 
            if t in type_descriptions
        }
        
        # Generiere den angepassten System-Prompt
        allowed_types_str = ', '.join(f"'{t}'" for t in enabled_types)

        # Füge NUR die aktivierten Typenbeschreibungen hinzu

            
        prompt_parts = [
            "Als KI-Assistent für Dokumentenanalyse ist deine Aufgabe, sensible Informationen in dem folgenden Text zu identifizieren.",
            "",
            "Für jede gefundene sensible Information gibst du zurück:",
            "- Den exakten Text",
            f"- Den Typ der Information (nur folgende Typen sind erlaubt: {allowed_types_str})",
            "- Die Position (Start-Index) im Text",
            "- Eine Konfidenz-Bewertung (0-1)",
            "- Eine kurze Begründung, warum es sich um diesen Typ handelt",
            "",
            "Du antwortest ausschließlich im JSON-Format:",
            "{",
            '    "document_type": "Dokumenttyp und kurze Begründung",',
            '    "findings": [',
            '        {',
            '            "text": "gefundener Text",',
            '            "type": "erlaubter_typ",',
            '            "start_index": position,',
            '            "confidence": konfidenz,',
            '            "reason": "Kurze Begründung, warum es sich um diesen Typ handelt"',
            '        }',
            '    ]',
            '}',
            'Dies sind die Typen, die du analysieren sollst:',
            ""
        ]
                
        for type_id, description in enabled_type_descriptions.items():
            prompt_parts.append(f"   - {description}")
            
        system_prompt = "\n".join(prompt_parts)
        
        # Erstelle den User-Prompt
        user_prompt = f"""Bitte analysiere folgenden Text: /n {text}"""
        
        # Log den kompletten Prompt
        logger.info("=== SYSTEM PROMPT ===")
        logger.info(system_prompt)
        logger.info("=== USER PROMPT ===")
        logger.info(user_prompt)
        logger.info("=== ENDE PROMPTS ===")

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        # Rufe Mistral mit Retry-Mechanismus auf
        chat_response = call_mistral_with_retry(
            messages=messages,
            model=MISTRAL_MODEL
        )
        
        # Parse die Antwort
        response_content = chat_response.choices[0].message.content
        
        try:
            findings_data = json.loads(response_content)
            
            # Vereinfache die Findings auf das Wesentliche: Text und Typ
            simplified_findings = []
            for finding in findings_data.get("findings", []):
                if finding.get('confidence', 0) >= CONFIDENCE_THRESHOLD:
                    simplified_findings.append({
                        'text': finding['text'].strip(),
                        'type': finding['type']
                    })
            
            logger.info(f"Extracted {len(simplified_findings)} findings with sufficient confidence")
            return simplified_findings
            
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in Mistral response: {e}")
            logger.error(f"Raw response: {response_content}")
            return []
        
    except Exception as e:
        logger.error(f"Fatal error in text analysis: {e}")
        notify_admin(
            "Text Analysis Failed",
            f"Critical error in analyze_text_with_mistral: {str(e)}"
        )
        return []

@app.route('/upload', methods=['POST'])
def upload_pdf():
    """Handle PDF upload and start processing."""
    try:
        if 'file' not in request.files:
            logger.error("No file provided in request")
            return jsonify({"error": "No file provided"}), 400
        
        file = request.files['file']
        if not file.filename.endswith('.pdf'):
            logger.error(f"Invalid file type: {file.filename}")
            return jsonify({"error": "File must be a PDF"}), 400
        
        # Get preferences from the request and ensure it's a dictionary
        try:
            preferences = json.loads(request.form.get('preferences', '{}'))
            if not isinstance(preferences, dict):
                logger.error("Preferences must be a JSON object")
                return jsonify({"error": "Preferences must be a JSON object"}), 400
            
            # Log frontend selection
            logger.info("Frontend selection for anonymization:")
            logger.info("-" * 80)
            logger.info("Selected options:")
            for option_id, is_enabled in preferences.items():
                if option_id in ANONYMIZATION_OPTIONS:
                    status = "ENABLED" if is_enabled else "disabled"
                    logger.info(f"  - {ANONYMIZATION_OPTIONS[option_id]['label']}: {status}")
                else:
                    logger.warning(f"  - Unknown option received: {option_id}")
            
            # Log options using defaults
            missing_options = set(ANONYMIZATION_OPTIONS.keys()) - set(preferences.keys())
            if missing_options:
                logger.info("\nOptions using default values:")
                for option_id in missing_options:
                    default_value = ANONYMIZATION_OPTIONS[option_id]['default']
                    status = "ENABLED" if default_value else "disabled"
                    logger.info(f"  - {ANONYMIZATION_OPTIONS[option_id]['label']}: {status} (default)")
                    preferences[option_id] = default_value
            logger.info("-" * 80)
            
        except json.JSONDecodeError:
            logger.error("Invalid JSON in preferences")
            return jsonify({"error": "Invalid JSON in preferences"}), 400
        
        # Read the PDF file
        pdf_data = file.read()
        logger.info(f"Read PDF file of size: {len(pdf_data)} bytes")
        
        # Start Celery task with explicit task name
        task = celery.send_task('app.process_pdf', args=[pdf_data, preferences])
        logger.info(f"Started task with ID: {task.id}")
        
        return jsonify({
            "task_id": task.id
        })
    
    except Exception as e:
        logger.error(f"Error in upload_pdf: {str(e)}")
        logger.exception("Full traceback:")
        return jsonify({"error": str(e)}), 500

@app.route('/status/<task_id>')
def get_status(task_id):
    """Get the status of a processing task."""
    try:
        task = process_pdf.AsyncResult(task_id)
        
        # If task is not ready yet
        if not task.ready():
            # Get progress information if available
            if task.state == 'PROGRESS':
                progress = task.info
                return jsonify({
                    "status": "Processing",
                    "current_page": progress.get('current_page', 0),
                    "total_pages": progress.get('total_pages', 0)
                })
            return jsonify({"status": "Processing"})
        
        # Get the result
        result = task.get()
        
        # If task failed
        if result.get('status') == 'Failed':
            return jsonify({
                "status": "Failed",
                "error": result.get('message', 'Unknown error occurred')
            })
        
        # If task completed successfully and has PDF data
        if result.get('status') == 'Completed' and 'pdf_data' in result:
            pdf_buffer = io.BytesIO(result['pdf_data'])
            response = send_file(
                pdf_buffer,
                mimetype='application/pdf',
                as_attachment=True,
                download_name=f'anonymized_{datetime.now().strftime("%Y%m%d_%H%M%S")}.pdf'
            )
            # Add CORS headers for file streaming
            response.headers['Access-Control-Expose-Headers'] = 'Content-Disposition'
            return response
        
        # If task completed but no PDF data (shouldn't happen normally)
        return jsonify({
            "status": "Completed",
            "message": result.get('message', 'Processing completed but no PDF data found'),
            "total_pages": result.get('total_pages', 0)
        })
        
    except Exception as e:
        logger.error(f"Error in get_status: {str(e)}")
        logger.exception("Full traceback:")
        return jsonify({
            "status": "Failed",
            "error": str(e)
        }), 500

if __name__ == '__main__':
    app.run(
        host=FLASK_HOST,
        port=FLASK_PORT
    )