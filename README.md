# PDF Anonymization API

This Flask-based API processes PDF files and anonymizes sensitive information using the Mistral AI API. It provides endpoints for uploading PDFs, tracking processing status, and downloading anonymized results.

## Prerequisites

- Python 3.8+
- Redis server
- Mistral API key

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Configure environment variables:
- Copy `.env.example` to `.env`
- Add your Mistral API key to `.env`
- Update Redis configuration if needed

3. Start Redis server:
```bash
redis-server
```

4. Start Celery worker:
```bash
celery -A app.celery worker --loglevel=info
```

5. Run the Flask application:
```bash
python app.py
```

## API Endpoints

### Upload PDF
- **URL**: `/upload`
- **Method**: `POST`
- **Form Data**:
  - `file`: PDF file
  - `preferences`: JSON string with anonymization preferences
- **Response**: Task ID for tracking progress

### Check Status
- **URL**: `/status/<task_id>`
- **Method**: `GET`
- **Response**: Processing status and result information

### Download Result
- **URL**: `/download/<filename>`
- **Method**: `GET`
- **Response**: Anonymized PDF file

## Usage Example

```python
import requests

# Upload PDF
files = {'file': open('document.pdf', 'rb')}
preferences = '{"anonymize_names": true, "anonymize_phones": true}'
response = requests.post('http://localhost:5000/upload', 
                        files=files,
                        data={'preferences': preferences})
task_id = response.json()['task_id']

# Check status
status = requests.get(f'http://localhost:5000/status/{task_id}')

# Download result when ready
if status.json()['status'] == 'Completed':
    filename = status.json()['result']['filename']
    result = requests.get(f'http://localhost:5000/download/{filename}')
    with open('anonymized.pdf', 'wb') as f:
        f.write(result.content)
``` 