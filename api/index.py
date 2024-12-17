# api/index.py
#
# This script processes emails from Kindle Scribe notebooks:
# 1. Finds unread emails from Kindle in Gmail
# 2. Downloads attached PDF and TXT files
# 3. Saves files to "Kindle Notebooks" folder in Google Drive
# 4. Archives the processed emails
# 5. Returns processing status and file IDs
#
# The script is designed to be called via Vercel's serverless functions
# and is triggered by GitHub Actions every 10 minutes.

import os
import json
import re
import requests
from base64 import urlsafe_b64decode
from urllib.parse import unquote
from bs4 import BeautifulSoup
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from http.server import BaseHTTPRequestHandler
from datetime import datetime
from googleapiclient.http import MediaIoBaseUpload
from io import BytesIO
from pytz import timezone
from google.auth.transport.requests import Request

SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',  # Read Gmail messages
    'https://www.googleapis.com/auth/gmail.modify',    # Archive emails
    'https://www.googleapis.com/auth/drive.file'      # Create/update Drive files
]

def get_services():
    """Initialize Gmail and Drive services using environment credentials.
    
    Uses GMAIL_TOKEN from environment variables, which should contain
    a JSON string with OAuth credentials. The token can be generated
    using gmail_token_generator.py.
    
    Returns:
        tuple: (gmail_service, drive_service) authenticated service objects
    
    Raises:
        ValueError: If GMAIL_TOKEN is missing or invalid
        Exception: For other initialization errors
    """
    try:
        token_json = os.environ.get('GMAIL_TOKEN')
        if not token_json:
            raise ValueError("GMAIL_TOKEN environment variable not set")
        
        creds_dict = json.loads(token_json)
        creds = Credentials.from_authorized_user_info(creds_dict, SCOPES)

        # Check if credentials are expired and can be refreshed
        if creds and creds.expired and creds.refresh_token:
            # Refresh the token
            creds.refresh(Request())
        
        # Now creds should be valid if refresh was successful
        if not creds or not creds.valid:
            raise ValueError("Invalid credentials. Could not validate or refresh token.")

        gmail_service = build('gmail', 'v1', credentials=creds)
        drive_service = build('drive', 'v3', credentials=creds)
        return gmail_service, drive_service
    except Exception as e:
        raise Exception(f"Failed to initialize services: {str(e)}")
        
def find_kindle_emails(service):
    """Find all unread Kindle emails matching our pattern.
    
    Searches Gmail for unread emails with specific subject line patterns
    that indicate they're from Kindle Scribe's "Convert to text and email" feature.
    
    Args:
        service: Authenticated Gmail service object
    
    Returns:
        list: Message objects containing IDs of matching emails
        
    Raises:
        Exception: If Gmail API query fails
    """
    try:
        query = 'subject:"you sent a file" "from your kindle" is:unread'
        result = service.users().messages().list(userId='me', q=query).execute()
        messages = result.get('messages', [])
        return messages if messages else []
    except Exception as e:
        raise Exception(f"Error finding Kindle emails: {str(e)}")

def mark_as_read_and_archive(service, msg_id):
    """Mark an email as read and archive it by removing UNREAD and INBOX labels."""
    try:
        service.users().messages().modify(
            userId='me',
            id=msg_id,
            body={'removeLabelIds': ['UNREAD', 'INBOX']}
        ).execute()
    except Exception as e:
        raise Exception(f"Error marking email as read and archived: {str(e)}")

def extract_email_data(service, msg_id):
    """Extract subject, filename, and HTML body from the email.
    
    Retrieves full email content and parses:
    - Subject line to get original notebook filename
    - HTML body containing download links
    
    Args:
        service: Authenticated Gmail service
        msg_id: Email message ID to process
    
    Returns:
        tuple: (filename, html_body)
        - filename: Original notebook name from subject
        - html_body: Decoded HTML content with download links
        
    Raises:
        ValueError: If email lacks required content
        Exception: For API or parsing errors
    """
    try:
        email_data = service.users().messages().get(
            userId='me', id=msg_id, format='full').execute()
        
        headers = email_data['payload']['headers']
        subject = next(h['value'] for h in headers if h['name'].lower() == 'subject')
        filename_match = re.search(r'"([^"]+)"', subject)
        filename = filename_match.group(1) if filename_match else "kindle_download"
        
        html_body = None
        parts = email_data.get('payload', {}).get('parts', [])
        for part in parts:
            if part.get('mimeType') == 'text/html':
                data = part.get('body', {}).get('data')
                if data:
                    html_body = urlsafe_b64decode(data).decode('utf-8')
                    break
        
        if not html_body:
            raise ValueError("No HTML content found in email")
            
        return filename, html_body
        
    except Exception as e:
        raise Exception(f"Error extracting email data: {str(e)}")

def extract_file_urls(html_body):
    """Extract PDF and text file download links from email HTML.
    
    Parses HTML to find download links for:
    1. PDF version of notebook (required)
    2. Text version of notebook (optional)
    
    Handles both direct links and Amazon's redirect URLs.
    
    Args:
        html_body: Raw HTML content from email
        
    Returns:
        tuple: (pdf_url, txt_url)
        - pdf_url: Direct download URL for PDF
        - txt_url: Direct download URL for text file, or None
        
    Raises:
        ValueError: If PDF link not found
    """
    if not html_body:
        raise ValueError("No HTML content in the email.")
    
    soup = BeautifulSoup(html_body, 'html.parser')
    pdf_url = None
    txt_url = None
    
    # Search all links
    for link in soup.find_all('a'):
        link_text = link.get_text().strip()
        href = link.get('href')
        
        if not href:
            continue
            
        # Check for PDF link
        if re.search(r'Download.*PDF', link_text, re.IGNORECASE):
            if 'amazon.com/gp/f.html' in href:
                encoded_url = re.search(r'&U=(.+?)&', href)
                if encoded_url:
                    pdf_url = unquote(encoded_url.group(1))
            else:
                pdf_url = href
                
        # Check for text file link
        elif re.search(r'Download.*text.*file', link_text, re.IGNORECASE):
            if 'amazon.com/gp/f.html' in href:
                encoded_url = re.search(r'&U=(.+?)&', href)
                if encoded_url:
                    txt_url = unquote(encoded_url.group(1))
            else:
                txt_url = href
    
    if not pdf_url:
        raise ValueError("No PDF download link found in the email body")
    
    return pdf_url, txt_url

def get_or_create_folder(drive_service, folder_name, parent_id=None):
    """Get or create a folder in Google Drive, optionally within a parent folder.
    
    This function:
    1. Searches for existing folder by name
    2. Creates new folder if none exists
    3. Handles nested folders via parent_id
    
    Args:
        drive_service: Authenticated Google Drive service
        folder_name: Name of folder to find/create
        parent_id: Optional ID of parent folder for nesting
    
    Returns:
        str: ID of existing or newly created folder
        
    Raises:
        Exception: If folder operations fail
    """
    try:
        # Build query to find existing folder
        query = f"mimeType='application/vnd.google-apps.folder' and trashed=false"
        if parent_id:
            query += f" and '{parent_id}' in parents"
        query += f" and name='{folder_name}'"
            
        response = drive_service.files().list(
            q=query,
            spaces='drive',
            fields='files(id, name)'
        ).execute()
        
        # If folder exists, return its ID
        if response.get('files'):
            return response['files'][0]['id']
            
        # If folder doesn't exist, create it
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        if parent_id:
            file_metadata['parents'] = [parent_id]
            
        file = drive_service.files().create(
            body=file_metadata,
            fields='id'
        ).execute()
        return file.get('id')
            
    except Exception as e:
        raise Exception(f"Error handling folder: {str(e)}")

def upload_to_drive(drive_service, file_content, filename, file_type='pdf'):
    """Upload file to Google Drive, moving existing files to an 'Old' subfolder.
    
    This function:
    1. Gets/creates main "Kindle Notebooks" folder
    2. Gets/creates "Old" subfolder for archiving
    3. If file exists with same name:
       - Moves existing file to Old folder
       - Adds timestamp to filename
    4. Uploads new file to main folder
    
    Args:
        drive_service: Authenticated Drive service
        file_content: Binary content of file to upload
        filename: Base name for the file (without extension)
        file_type: Either 'pdf' or 'txt' (default: 'pdf')
    
    Returns:
        str: ID of newly uploaded file
        
    Raises:
        Exception: If upload or file operations fail
    """
    try:
        # Get or create main folder
        main_folder_id = get_or_create_folder(drive_service, "Kindle Notebooks")
        
        # Get or create 'Old' subfolder
        old_folder_id = get_or_create_folder(drive_service, "Old", parent_id=main_folder_id)
        
        extension = '.pdf' if file_type == 'pdf' else '.txt'
        mimetype = 'application/pdf' if file_type == 'pdf' else 'text/plain'
        full_filename = f'{filename}{extension}'
        
        # Check if file already exists
        existing_file_query = f"name='{full_filename}' and '{main_folder_id}' in parents and trashed=false"
        existing_files = drive_service.files().list(
            q=existing_file_query,
            spaces='drive',
            fields='files(id, name)'
        ).execute().get('files', [])
        
        # If file exists, move it to Old folder with timestamp
        if existing_files:
            try:
                # Try to get EST timestamp
                est_time = datetime.now().astimezone(timezone('US/Eastern'))
            except:
                # Fallback to UTC if timezone conversion fails
                est_time = datetime.utcnow()
            
            timestamp = est_time.strftime('%Y%m%d_%H%M%S')
            
            for existing_file in existing_files:
                new_name = f"{filename}_{timestamp}{extension}"
                print(f"Moving existing file {existing_file['name']} to Old folder as {new_name}")
                
                # Update file metadata (rename and move)
                drive_service.files().update(
                    fileId=existing_file['id'],
                    addParents=old_folder_id,
                    removeParents=main_folder_id,
                    body={'name': new_name}
                ).execute()
        
        # Upload new file
        print(f"Uploading new file: {full_filename}")
        file_metadata = {
            'name': full_filename,
            'parents': [main_folder_id]
        }
        media = MediaIoBaseUpload(
            BytesIO(file_content),
            mimetype=mimetype,
            resumable=True
        )
        
        file = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id'
        ).execute()
        
        print(f"Successfully uploaded new file with ID: {file.get('id')}")
        return file.get('id')
        
    except Exception as e:
        print(f"Error in upload_to_drive: {str(e)}")
        raise Exception(f"Error uploading to Drive: {str(e)}")

def process_kindle_emails():
    """Process all unread Kindle emails and their attachments.
    
    Main processing flow:
    1. Initialize Gmail and Drive services
    2. Find unread Kindle emails
    3. For each email:
       - Extract filename and download links
       - Download PDF and TXT attachments
       - Upload files to Google Drive
       - Archive the email
    4. Track processing status and errors
    
    Performance notes:
    - Logs timing information for debugging
    - Skips duplicate filenames in same batch
    - Processes all files from an email before moving to next
    
    Returns:
        dict: Response with:
            - statusCode: 200 for success, 500 for errors
            - body: JSON with processing details:
                - message: Status description
                - status: 'success', 'no_email', or 'error'
                - files_processed: List of processed files
                - processing_time: Total execution time
                - timestamp: Processing completion time
    """
    try:
        start_time = datetime.now()
        print(f"Starting process at {start_time}")
        
        gmail_service, drive_service = get_services()
        print(f"Services initialized after {datetime.now() - start_time}")
        
        messages = find_kindle_emails(gmail_service)
        if not messages:
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'No unread Kindle emails found',
                    'status': 'no_email',
                    'timestamp': str(datetime.now())
                })
            }
        
        print(f"Found {len(messages)} messages after {datetime.now() - start_time}")
        
        processed_files = []
        processed_filenames = set()
        
        for message in messages:
            try:
                msg_id = message['id']
                print(f"Processing message {msg_id} at {datetime.now() - start_time}")
                
                filename, html_body = extract_email_data(gmail_service, msg_id)
                
                if filename in processed_filenames:
                    print(f"Skipping duplicate filename: {filename}")
                    continue
                    
                processed_filenames.add(filename)
                mark_as_read_and_archive(gmail_service, msg_id)
                pdf_url, txt_url = extract_file_urls(html_body)
                
                print(f"Downloading PDF for {filename} at {datetime.now() - start_time}")
                response = requests.get(pdf_url, timeout=30)
                response.raise_for_status()
                
                print(f"Uploading PDF to Drive at {datetime.now() - start_time}")
                pdf_id = upload_to_drive(drive_service, response.content, filename, 'pdf')
                
                txt_id = None
                if txt_url:
                    print(f"Downloading TXT for {filename} at {datetime.now() - start_time}")
                    response = requests.get(txt_url, timeout=30)
                    response.raise_for_status()
                    
                    print(f"Uploading TXT to Drive at {datetime.now() - start_time}")
                    txt_id = upload_to_drive(drive_service, response.content, filename, 'txt')
                
                processed_files.append({
                    'filename': filename,
                    'pdf_file_id': pdf_id,
                    'txt_file_id': txt_id,
                    'status': 'success'
                })
                
            except Exception as e:
                processed_files.append({
                    'message_id': message.get('id', 'unknown'),
                    'error': str(e),
                    'status': 'error'
                })
        
        total_time = datetime.now() - start_time
        print(f"Completed processing in {total_time}")
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Processing complete',
                'status': 'success',
                'files_processed': processed_files,
                'processing_time': str(total_time),
                'timestamp': str(datetime.now())
            })
        }
        
    except Exception as e:
        return {
            'statusCode': 500,
            'body': json.dumps({
                'message': str(e),
                'status': 'error',
                'timestamp': str(datetime.now())
            })
        }

class handler(BaseHTTPRequestHandler):
    """HTTP request handler for Vercel serverless function.
    
    Handles GET requests to process Kindle emails:
    1. Calls process_kindle_emails()
    2. Returns JSON response with processing results
    3. Sets appropriate HTTP status code
    
    Note: This handler is specifically for Vercel's serverless
    environment and follows their expected response format.
    """
    
    def do_GET(self):
        """Handle GET requests to process Kindle emails.
        
        Returns:
            HTTP Response with:
            - Status code (200/500)
            - JSON content type
            - Processing results in response body
        """
        result = process_kindle_emails()
        
        self.send_response(result.get('statusCode', 500))
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        
        response_body = result.get('body', json.dumps({'error': 'Unknown error'}))
        self.wfile.write(response_body.encode())
        return

if __name__ == "__main__":
    print(json.dumps(process_kindle_emails(), indent=2))