import os
import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from .storage import store_for_processing
from .utils import get_services
from .distill_text import download_file_content, call_openai_api

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Handle GET requests to process queued files."""
        try:
            # Parse query parameters
            query = urlparse(self.path).query
            params = parse_qs(query)
            temp_id = params.get('temp_id', [None])[0]
            
            if not temp_id:
                raise ValueError("temp_id parameter is required")
            
            print(f"Processing queued file with temp_id: {temp_id}")
            drive_service = get_services()
            
            # Get content from temp storage
            print("Retrieving content from temporary storage")
            content = download_file_content(drive_service, temp_id)
            
            # Process with OpenAI
            print("Processing with OpenAI")
            md_content = call_openai_api(content, drive_service)
            
            # Store result in temp folder
            print("Storing processed result")
            result_id = store_for_processing(drive_service, md_content, f'result_{temp_id}')
            print(f"Result stored with ID: {result_id}")
            
            response = {
                'status': 'processed',
                'result_id': result_id
            }
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())
            
        except Exception as e:
            print(f"Error processing queued file: {str(e)}")
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode()) 