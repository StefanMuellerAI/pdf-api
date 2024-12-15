import os
from pathlib import Path
from enum import Enum
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()

# Basic Configuration
FLASK_HOST = os.getenv('FLASK_HOST', '0.0.0.0')
FLASK_PORT = int(os.getenv('FLASK_PORT', 5001))

# Logging Configuration
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

# Cache Configuration
CACHE_DIR = Path(os.getenv('CACHE_DIR', 'cache'))
CACHE_FILE = CACHE_DIR / 'anonymization_options.pickle'
CACHE_VALIDITY = timedelta(hours=24)  # Cache-Gültigkeit: 24 Stunden

# Default Anonymization Options
DEFAULT_MINIMUM_OPTIONS = {
    'addresses': True,      # Postadressen
    'dates': True,         # Datumswerte
    'emails': True,        # E-Mail-Adressen
    'ids': True,          # Identifikationsnummern
    'names': True,         # Personennamen
    'phone_numbers': True, # Telefonnummern
}
# Log Level Configuration
class LogLevel(Enum):
    """Definiert verfügbare Log-Level für verschiedene Komponenten."""
    MINIMAL = 'MINIMAL'    # Nur kritische Fehler und wichtige Infos
    STANDARD = 'STANDARD'  # Standard-Logging ohne sensible Daten
    DEBUG = 'DEBUG'       # Ausführliches Logging (nur für Entwicklung)

# Component-specific Log Levels
COMPONENT_LOG_LEVELS = {
    'pdf_processing': os.getenv('PDF_PROCESSING_LOG_LEVEL', 'STANDARD'),
    'api': os.getenv('API_LOG_LEVEL', 'STANDARD'),
    'mistral': os.getenv('MISTRAL_LOG_LEVEL', 'STANDARD'),
    'anonymization': os.getenv('ANONYMIZATION_LOG_LEVEL', 'STANDARD')
}

# Timeout Configuration
MISTRAL_TIMEOUT = int(os.getenv('MISTRAL_TIMEOUT', 30))  # Sekunden
SUPABASE_TIMEOUT = int(os.getenv('SUPABASE_TIMEOUT', 10))
REDIS_TIMEOUT = int(os.getenv('REDIS_TIMEOUT', 5))

# Retry Configuration
MAX_RETRIES = int(os.getenv('MAX_RETRIES', 3))
INITIAL_WAIT = float(os.getenv('INITIAL_WAIT', 1))  # Sekunden
MAX_WAIT = float(os.getenv('MAX_WAIT', 10))  # Sekunden

# Admin Notification Configuration
ADMIN_EMAIL = os.getenv('ADMIN_EMAIL')
SMTP_HOST = os.getenv('SMTP_HOST')
SMTP_PORT = int(os.getenv('SMTP_PORT', 587))
SMTP_USER = os.getenv('SMTP_USER')
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD')

# Redis Configuration
REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
REDIS_PORT = os.getenv('REDIS_PORT', '6379')
REDIS_DB = os.getenv('REDIS_DB', '0')
REDIS_PASSWORD = os.getenv('REDIS_PASSWORD', 'redis123')
REDIS_URL = f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"

# Mistral Configuration
MISTRAL_API_KEY = os.getenv('MISTRAL_API_KEY')
MISTRAL_MODEL = os.getenv('MISTRAL_MODEL', 'mistral-large-latest')

# PDF Processing Configuration
MAX_PDF_PAGES = int(os.getenv('MAX_PDF_PAGES', 10))
REDACTION_FILL_COLOR = tuple(map(int, os.getenv('REDACTION_FILL_COLOR', '0,0,0').split(',')))

# Schema Configuration
FINDING_SCHEMA = {
    "type": "object",
    "properties": {
        "document_type": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["text", "type", "start_index", "confidence", "reason"],
                "properties": {
                    "text": {"type": "string", "minLength": 1},
                    "type": {"type": "string"},
                    "start_index": {"type": "integer", "minimum": 0},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "reason": {"type": "string", "minLength": 1}
                }
            }
        }
    }
}

# Default System Prompt
DEFAULT_SYSTEM_PROMPT = """Als KI-Assistent für Dokumentenanalyse ist es meine Aufgabe, sensible Informationen in Texten zu identifizieren.

Für jede gefundene sensible Information gebe ich zurück:
- Den exakten Text
- Den Typ der Information (nur folgende Typen sind erlaubt: 'addresses', 'dates', 'emails', 'ids', 'names', 'phone_numbers')
- Die Position (Start-Index) im Text
- Eine Konfidenz-Bewertung (0-1)
- Eine kurze Begründung, warum es sich um diesen Typ handelt

Ich antworte ausschließlich im JSON-Format:
{
    "document_type": "Dokumenttyp und kurze Begründung",
    "findings": [
        {
            "text": "gefundener Text",
            "type": "erlaubter_typ",
            "start_index": position,
            "confidence": konfidenz,
            "reason": "Kurze Begründung, warum es sich um diesen Typ handelt"
        }
    ]
}

Wichtige Regeln:
1. Nur die folgenden Typen sind erlaubt:
   - 'addresses' für Postadressen
   - 'dates' für Datumswerte
   - 'emails' für E-Mail-Adressen
   - 'ids' für Identifikationsnummern (Steuer-IDs, Handelsregister, etc.)
   - 'names' für Personennamen
   - 'phone_numbers' für Telefon- und Faxnummern

2. Keine anderen Typen verwenden (wie 'Currency Amount', 'Company ID', etc.)
3. Exakte Textgrenzen ohne zusätzliche Whitespaces
4. Korrekte Start-Indizes
5. Realistische Konfidenzwerte
6. Vermeidung von Falsch-Positiven
7. Für jedes Finding eine kurze, präzise Begründung angeben"""

# Celery Configuration
CELERY_CONFIG = {
    'broker_transport_options': {'visibility_timeout': REDIS_TIMEOUT},
    'redis_socket_timeout': REDIS_TIMEOUT,
    'redis_socket_connect_timeout': REDIS_TIMEOUT,
    'broker_connection_retry': True,
    'broker_connection_max_retries': MAX_RETRIES,
    'worker_max_tasks_per_child': 1,
    'worker_prefetch_multiplier': 1,
    'worker_pool': 'solo',
    'task_serializer': 'pickle',
    'accept_content': ['pickle', 'json'],
    'result_serializer': 'pickle',
    'task_default_queue': 'pdf_tasks',
    'task_routes': {
        'app.process_pdf': {'queue': 'pdf_tasks'}
    }
}

# Confidence threshold for findings
CONFIDENCE_THRESHOLD = 0.8  # 90% minimum confidence

API_TOKEN = os.getenv('API_TOKEN', 'your-default-secure-token-here')  # Make sure to change this in production


DEFAULT_MINIMUM_OPTIONS = {
    'names': True,
    'addresses': False,
    'dates': False,
    'emails': True,
    'phone_numbers': True,
    'ids': False
}

# Initialisiere ANONYMIZATION_OPTIONS mit Standardwerten
ANONYMIZATION_OPTIONS = {
    option_id: {
        'id': option_id,
        'label': option_id.replace('_', ' ').title(),
        'description': f"Detect and redact {option_id.replace('_', ' ')}",
        'default': is_default
    }
    for option_id, is_default in DEFAULT_MINIMUM_OPTIONS.items()
}

TYPE_DESCRIPTIONS = {
    'addresses': "'addresses' für vollständige Postadressen (NICHT einzelne Straßennamen oder Städte, sondern nur komplette Adressen mit mindestens Straße, Hausnummer und PLZ/Ort)",
    'dates': "'dates' für Datumswerte in verschiedenen Formaten (z.B., '01.01.2024', '2024-01-01', '1. Januar 2024'). NICHT markieren: Uhrzeiten ohne Datum, Jahreszahlen allein",
    'emails': "'emails' für gültige E-Mail-Adressen mit @ und Domain (z.B., 'beispiel@domain.de'). NICHT markieren: Texte mit '@' die keine gültigen E-Mail-Adressen sind",
    'ids': "'ids' für eindeutige Identifikationsnummern (nur Steuer-IDs, Sozialversicherungsnummern, Handelsregisternummern, Ausweisnummern). NICHT markieren: Bestellnummern, Artikelnummern, Rechnungsnummern",
    'names': "'names' ausschließlich für echte Personennamen von Menschen (z.B., 'Dr. Max Mustermann, Stefan Müller; Müller, Stefan; Stefan M.; S. Meier'). NICHT markieren: Firmennamen, Produktnamen, Straßennamen, Städtenamen, Gebäudenamen oder andere Eigennamen",
    'phone_numbers': "'phone_numbers' für Telefon- und Faxnummern mit Vorwahl (z.B., '+49 30 12345678; 0177-5228242;0177/5228242;01775228242'). NICHT markieren: andere Zahlenkombinationen oder Nummern ohne Vorwahl"
}
        
