from config import *
import io
from datetime import datetime
import logging
from flask import Flask, request, send_file, jsonify
from flask_cors import CORS
import json
from datetime import datetime
from typing import List, Dict, Any, Tuple
from functools import wraps
from celery_app import celery
from tasks import process_pdf

# Configure logging
logging.basicConfig(
    level=LOG_LEVEL,
    format=LOG_FORMAT
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
CORS(app)

def require_token(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({"error": "Authentication required."}), 401
        
        if token != f"Bearer {API_TOKEN}":
            return jsonify({"error": "Invalid token."}), 401
            
        return f(*args, **kwargs)
    return decorated

@app.route('/upload', methods=['POST'])
@require_token
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
        
        # Get preferences from the request
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
        
        # Start Celery task
        task = process_pdf.delay(pdf_data, preferences)
        logger.info(f"Started task with ID: {task.id}")
        
        return jsonify({
            "task_id": task.id
        })
    
    except Exception as e:
        logger.error(f"Error in upload_pdf: {str(e)}")
        logger.exception("Full traceback:")
        return jsonify({"error": str(e)}), 500

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