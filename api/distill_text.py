# distill_text.py

import os
import json
import re
import openai
from datetime import datetime
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from io import BytesIO
from http.server import BaseHTTPRequestHandler
from pytz import timezone

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request

from openai import OpenAI

SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/drive.file'
]

def get_services():
    """Initialize Drive service using environment credentials."""
    token_json = os.environ.get('GMAIL_TOKEN')
    if not token_json:
        raise ValueError("GMAIL_TOKEN environment variable not set")

    creds_dict = json.loads(token_json)
    creds = Credentials.from_authorized_user_info(creds_dict, SCOPES)

    # Refresh token if needed
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())

    if not creds or not creds.valid:
        raise ValueError("Invalid credentials.")

    drive_service = build('drive', 'v3', credentials=creds)
    return drive_service

def get_or_create_folder(drive_service, folder_name, parent_id=None):
    """Get or create a folder by name."""
    query = f"mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    query += f" and name='{folder_name}'"

    response = drive_service.files().list(
        q=query,
        spaces='drive',
        fields='files(id, name)'
    ).execute()

    # If folder exists
    if response.get('files'):
        return response['files'][0]['id']

    # Create folder if not found
    file_metadata = {
        'name': folder_name,
        'mimeType': 'application/vnd.google-apps.folder'
    }
    if parent_id:
        file_metadata['parents'] = [parent_id]

    folder = drive_service.files().create(
        body=file_metadata,
        fields='id'
    ).execute()
    return folder.get('id')

def list_txt_files(drive_service):
    """List all .txt files in Kindle Notebooks folder."""
    # Get or create Kindle Notebooks folder
    main_folder_id = get_or_create_folder(drive_service, "Kindle Notebooks")

    query = f"mimeType='text/plain' and '{main_folder_id}' in parents and trashed=false"
    response = drive_service.files().list(
        q=query,
        spaces='drive',
        fields='files(id, name, modifiedTime)'
    ).execute()

    files = response.get('files', [])
    return files, main_folder_id

def download_file_content(drive_service, file_id):
    """Download file content from Drive."""
    request = drive_service.files().get_media(fileId=file_id)
    fh = BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while done is False:
        status, done = downloader.next_chunk()
    fh.seek(0)
    return fh.read().decode('utf-8', errors='replace')

def call_openai_api(text):
    """Call OpenAI with the text to get summary and action items."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable not set.")
    
    client = OpenAI(api_key=api_key)

    prompt = (
        "You are a helpful assistant. Given the following text, please:\n"
        "1. Extract any action items or to-dos. Only include items that are specifically called out directly as followups - don't include items that are only indirectly implied, and don't try too hard to infer.\n"
        "2. Summarize the text. Make it concise and actionable. Ensure you do not speculate. If there isn't enough information for you to understand what something means, don't guess.\n"
        "3. Include the date in the summary, if found in the notes.\n"
        "Output the result in Markdown format with the following sections:\n"
        "### Summary\n\n"
        "### Action Items\n\n"
        "### Notes\n\n"
        "The 'Notes' section should be exactly equivalent to ALL of the original text you were sent to process. However, you may try to correct errors in OCR - don't get creative, but if you see a word that almost certainly should have been something else given the context, you maycorrect it.\n\n"
        "Text to process:\n" + text
    )

    response = client.chat.completions.create(
        model="gpt-4o",  # or "gpt-4-turbo-preview" for the latest version
        messages=[
            {
                "role": "system", 
                "content": "You are a helpful assistant who summarizes OCR'd handwritten notes, primarily from meetings. Sometimes there are typos from the OCR, and they're generally in shorthand. You do your best."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        max_tokens=10000,
        temperature=0.5
    )

    return response.choices[0].message.content.strip()

def upload_markdown(drive_service, filename, content):
    """Upload the markdown file to the main folder, handle duplicates as in the original code."""
    main_folder_id = get_or_create_folder(drive_service, "Kindle Notebooks")
    old_folder_id = get_or_create_folder(drive_service, "Old", parent_id=main_folder_id)

    extension = '.md'
    full_filename = f'{filename}{extension}'

    # Check if file already exists
    existing_file_query = f"name='{full_filename}' and '{main_folder_id}' in parents and trashed=false"
    existing_files = drive_service.files().list(
        q=existing_file_query,
        spaces='drive',
        fields='files(id, name)'
    ).execute().get('files', [])

    if existing_files:
        try:
            est_time = datetime.now().astimezone(timezone('US/Eastern'))
        except:
            est_time = datetime.utcnow()
        timestamp = est_time.strftime('%Y%m%d_%H%M%S')

        for existing_file in existing_files:
            new_name = f"{filename}_{timestamp}{extension}"
            drive_service.files().update(
                fileId=existing_file['id'],
                addParents=old_folder_id,
                removeParents=main_folder_id,
                body={'name': new_name}
            ).execute()

    file_metadata = {
        'name': full_filename,
        'parents': [main_folder_id]
    }
    media = MediaIoBaseUpload(
        BytesIO(content.encode('utf-8')),
        mimetype='text/markdown',
        resumable=True
    )
    file = drive_service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id'
    ).execute()

    return file.get('id')

def move_original_file(drive_service, file_id, filename, main_folder_id):
    """Move the original .txt file to 'Old' folder with timestamped rename."""
    old_folder_id = get_or_create_folder(drive_service, "Old", parent_id=main_folder_id)
    try:
        est_time = datetime.now().astimezone(timezone('US/Eastern'))
    except:
        est_time = datetime.utcnow()
    timestamp = est_time.strftime('%Y%m%d_%H%M%S')

    # Extract base filename (remove extension)
    base_name = re.sub(r'\.txt$', '', filename, flags=re.IGNORECASE)
    new_name = f"{base_name}_{timestamp}.txt"

    drive_service.files().update(
        fileId=file_id,
        addParents=old_folder_id,
        removeParents=main_folder_id,
        body={'name': new_name}
    ).execute()

def process_text_files():
    try:
        drive_service = get_services()
        txt_files, main_folder_id = list_txt_files(drive_service)

        if not txt_files:
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'No new text files found',
                    'status': 'no_text_files',
                    'timestamp': str(datetime.now())
                })
            }

        processed = []
        for f in txt_files:
            file_id = f['id']
            filename = f['name']
            # Download content
            text_content = download_file_content(drive_service, file_id)
            # Call OpenAI
            md_content = call_openai_api(text_content)
            # Derive a base filename for the md file
            base_name = re.sub(r'\.txt$', '', filename, flags=re.IGNORECASE)
            # Upload MD file
            md_file_id = upload_markdown(drive_service, base_name, md_content)
            # Move original txt to Old
            move_original_file(drive_service, file_id, filename, main_folder_id)
            processed.append({
                'original_txt': filename,
                'md_file_id': md_file_id,
                'status': 'success'
            })

        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Text files processed successfully',
                'status': 'success',
                'processed': processed,
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
    def do_GET(self):
        result = process_text_files()

        self.send_response(result.get('statusCode', 500))
        self.send_header('Content-type', 'application/json')
        self.end_headers()

        response_body = result.get('body', json.dumps({'error': 'Unknown error'}))
        self.wfile.write(response_body.encode())
        return
