from config import *
import io
from datetime import datetime
import logging
from flask import Flask, request, send_file, jsonify
from flask_cors import CORS
import json
from datetime import datetime
from typing import List, Dict, Any, Tuple
import fitz  # PyMuPDF
import tempfile
from celery_app import celery
from tasks import process_pdf
from security import require_token
# Configure logging
logging.basicConfig(
    level=LOG_LEVEL,
    format=LOG_FORMAT
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
CORS(app, expose_headers=['Content-Type', 'Authorization'])

@app.route('/upload', methods=['POST'])
@require_token
def upload_pdf():
    """Handle PDF upload and start processing."""
    try:
        if 'file' not in request.files:
            logger.error("No file provided in request")
            return jsonify({
                "error": "No file provided",
                "message": "Please select a PDF file to upload.",
                "details": {
                    "suggestion": "Make sure you have selected a PDF file before clicking upload."
                }
            }), 400
        
        file = request.files['file']
        if not file.filename.endswith('.pdf'):
            logger.error(f"Invalid file type: {file.filename}")
            return jsonify({
                "error": "Invalid file type",
                "message": "The uploaded file must be a PDF document.",
                "details": {
                    "filename": file.filename,
                    "suggestion": "Please select a file with .pdf extension."
                }
            }), 400
        
        # Read the PDF file once
        pdf_data = file.read()
        logger.info(f"Read PDF file of size: {len(pdf_data)} bytes")
        
        # Check page count
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=True) as temp_file:
            temp_file.write(pdf_data)
            temp_file.flush()
            
            try:
                doc = fitz.open(temp_file.name)
                page_count = len(doc)
                doc.close()
                
                if page_count > MAX_PDF_PAGES:
                    logger.error(f"PDF has too many pages: {page_count}")
                    return jsonify({
                        "error": "PDF exceeds maximum page limit",
                        "message": f"The PDF file contains {page_count} pages, but the maximum allowed is {MAX_PDF_PAGES} pages.",
                        "details": {
                            "current_pages": page_count,
                            "max_pages": MAX_PDF_PAGES,
                            "suggestion": "Please split the document into smaller parts or contact support if you need to process larger documents."
                        }
                    }), 400
                    
            except Exception as e:
                logger.error(f"Error checking PDF page count: {str(e)}")
                return jsonify({
                    "error": "Invalid PDF file",
                    "message": "The uploaded file appears to be corrupted or is not a valid PDF.",
                    "details": {
                        "technical_error": str(e),
                        "suggestion": "Please ensure the file is a valid PDF document and try again."
                    }
                }), 400
        
        # Get preferences from the request
        try:
            preferences = json.loads(request.form.get('preferences', '{}'))
            if not isinstance(preferences, dict):
                logger.error("Preferences must be a JSON object")
                return jsonify({
                    "error": "Invalid preferences format",
                    "message": "The anonymization preferences are not in the correct format.",
                    "details": {
                        "suggestion": "Please try again or contact support if the problem persists."
                    }
                }), 400
            
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
            return jsonify({
                "error": "Invalid preferences data",
                "message": "The anonymization preferences contain invalid data.",
                "details": {
                    "suggestion": "Please try again or contact support if the problem persists."
                }
            }), 400
        
        # Start Celery task
        task = process_pdf.delay(pdf_data, preferences)
        logger.info(f"Started task with ID: {task.id}")
        
        return jsonify({
            "task_id": task.id,
            "message": "PDF upload successful. Processing started."
        })
    
    except Exception as e:
        logger.error(f"Error in upload_pdf: {str(e)}")
        logger.exception("Full traceback:")
        return jsonify({
            "error": "Internal server error",
            "message": "An unexpected error occurred while processing your request.",
            "details": {
                "technical_error": str(e),
                "suggestion": "Please try again later or contact support if the problem persists."
            }
        }), 500

@app.route('/status/<task_id>')
@require_token
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