import os
import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from .utils import get_services, get_or_create_folder
from .distill_text import (
    download_file_content, 
    upload_markdown,
    move_original_file
)

def cleanup_temp_files(drive_service, file_ids):
    """Delete temporary files after processing."""
    for file_id in file_ids:
        try:
            drive_service.files().delete(fileId=file_id).execute()
        except Exception as e:
            print(f"Warning: Failed to delete temp file {file_id}: {str(e)}")

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Handle GET requests to save processed files."""
        try:
            # Parse query parameters
            query = urlparse(self.path).query
            params = parse_qs(query)
            result_id = params.get('result_id', [None])[0]
            original_id = params.get('original_id', [None])[0]
            
            if not result_id or not original_id:
                raise ValueError("result_id and original_id parameters are required")
            
            print(f"Saving processed file: result_id={result_id}, original_id={original_id}")
            drive_service = get_services()
            
            # Get processed content
            print("Retrieving processed content")
            md_content = download_file_content(drive_service, result_id)
            
            # Get original filename
            file = drive_service.files().get(fileId=original_id, fields='name').execute()
            filename = file['name'].replace('.txt', '')
            
            # Save to final location
            print(f"Uploading markdown file: {filename}.md")
            upload_markdown(drive_service, filename, md_content)
            
            # Move original file
            print("Archiving original file")
            main_folder_id = get_or_create_folder(drive_service, "Kindle Notebooks")
            move_original_file(drive_service, original_id, filename, main_folder_id)
            
            # Clean up temp files
            print("Cleaning up temporary files")
            cleanup_temp_files(drive_service, [result_id])
            
            response = {'status': 'completed'}
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())
            
        except Exception as e:
            print(f"Error in save_processed: {str(e)}")
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode()) 