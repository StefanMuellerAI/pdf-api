#mistral.py
#Description: This file contains the Mistral API client and the functions to analyze text and pages with Mistral.
#Date: 2024-12-15

import json
import logging
from mistralai import Mistral
from encoding_utils import encode_page_as_base64
import os
from config import *

logger = logging.getLogger(__name__)

mistral_client = Mistral(api_key=MISTRAL_API_KEY)

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

        # Filtere die Beschreibungen - NUR für aktivierte Typen
        enabled_type_descriptions = {
            t: TYPE_DESCRIPTIONS[t] 
            for t in enabled_types 
            if t in TYPE_DESCRIPTIONS
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
        return []
    
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
        raise

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
