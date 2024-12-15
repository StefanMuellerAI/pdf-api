from celery_app import celery, signals
from config import *
import logging
import tempfile
import io
from utils import process_single_page
import fitz
import concurrent.futures

# Configure logging
logger = logging.getLogger(__name__)

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

@celery.task(name='pdf_api.tasks.process_pdf', bind=True)
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
        
        # Create arguments list for parallel processing
        page_args = [(doc[i], i, total_pages, preferences) for i in range(total_pages)]
        
        # Process pages in parallel
        processed_pages = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(os.cpu_count(), total_pages)) as executor:
            # Start parallel processing
            future_to_page = {executor.submit(process_single_page, args): args[1] for args in page_args}
            
            # Collect results and update progress
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