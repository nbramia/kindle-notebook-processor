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
import time

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

def get_prompt_from_drive(drive_service):
    """Get the prompt instructions from a markdown file in Drive."""
    max_retries = 3
    retry_delay = 2  # seconds

    main_folder_id = get_or_create_folder(drive_service, "Kindle Notebooks")
    
    # Look for the prompt file
    query = f"name='prompt_instructions.md' and '{main_folder_id}' in parents and trashed=false"
    response = drive_service.files().list(
        q=query,
        spaces='drive',
        fields='files(id, name)'
    ).execute()

    # If prompt file doesn't exist, create it with default instructions
    if not response.get('files'):
        default_prompt = (
            "You are a helpful assistant. Given the following text, please:\n"
            "1. Summarize the text. Make it concise and actionable. Ensure you do not speculate. If there isn't enough information for you to understand what something means, don't guess.\n"
            "2. Include the date in the summary, if found in the notes. Use bullet points and extremely concise language in the Summary section - no fluff, no extra words. Less than 100 words.\n"
            "3. Extract any action items or to-dos. Only include items that are specifically called out directly as followups - don't include items that are only indirectly implied, and don't try too hard to infer. If you find no action items, say 'No action items found'.\n"
            "4. The 'Notes' section should be exactly equivalent to ALL of the original text you were sent to process. However, you may try to correct errors in OCR - don't get creative, but if you see a word that almost certainly should have been something else given the context, you may correct it.\n"
            "5. The 'Insights' section is optional. IF you have any key insights, links to relevant articles, or other information that would likely be useful to the person who took these notes, you may include this section.\n"
            "Output the result in Markdown format, leveraging # to create section headers, hyphens followed by a space to create bullets, '- [ ]' to create checkboxes, and numerical lists. Do not start and end the .md file with '```' - simply display the formatted text itself. No tick marks. I'm going to open the resulting file in a markdown editor like Obsidian and I want it to be formatted nicely. Organize into following sections:\n"
            "### Summary\n\n"
            "### Action Items\n\n"
            "### Notes\n\n"
            "### Insights\n\n"
        )
        
        file_metadata = {
            'name': 'prompt_instructions.md',
            'parents': [main_folder_id]
        }
        media = MediaIoBaseUpload(
            BytesIO(default_prompt.encode('utf-8')),
            mimetype='text/markdown',
            resumable=True
        )
        file = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id'
        ).execute()

        # Add retry logic for reading the newly created file
        for attempt in range(max_retries):
            try:
                time.sleep(retry_delay)  # Wait before trying to read
                response = drive_service.files().list(
                    q=query,
                    spaces='drive',
                    fields='files(id, name)'
                ).execute()
                if response.get('files'):
                    break
            except Exception as e:
                if attempt == max_retries - 1:  # If last attempt
                    print(f"Unable to verify file creation after {max_retries} attempts. Using default prompt.")
                    return default_prompt
                continue

    # If prompt file exists, read its content with retry logic
    file_id = response['files'][0]['id']
    for attempt in range(max_retries):
        try:
            request = drive_service.files().get_media(fileId=file_id)
            fh = BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while done is False:
                status, done = downloader.next_chunk()
            fh.seek(0)
            return fh.read().decode('utf-8')
        except Exception as e:
            if attempt == max_retries - 1:  # If last attempt
                raise Exception(f"Failed to read prompt file after {max_retries} attempts: {str(e)}")
            time.sleep(retry_delay)
            continue

def call_openai_api(text, drive_service):
    """Call OpenAI with the text to get summary and action items."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable not set.")
    
    client = OpenAI(api_key=api_key)
    
    # Get prompt from Drive
    prompt = get_prompt_from_drive(drive_service)
    prompt += "\n\nText to process:\n" + text

    response = client.chat.completions.create(
        model="gpt-4o",
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
    """Upload the markdown file and move any matching PDF to Old folder."""
    main_folder_id = get_or_create_folder(drive_service, "Kindle Notebooks")
    old_folder_id = get_or_create_folder(drive_service, "Old", parent_id=main_folder_id)

    extension = '.md'
    full_filename = f'{filename}{extension}'
    pdf_filename = f'{filename}.pdf'

    # Check if markdown file already exists
    existing_file_query = f"name='{full_filename}' and '{main_folder_id}' in parents and trashed=false"
    existing_files = drive_service.files().list(
        q=existing_file_query,
        spaces='drive',
        fields='files(id, name)'
    ).execute().get('files', [])

    # Check for matching PDF file
    pdf_query = f"name='{pdf_filename}' and '{main_folder_id}' in parents and trashed=false"
    pdf_files = drive_service.files().list(
        q=pdf_query,
        spaces='drive',
        fields='files(id, name)'
    ).execute().get('files', [])

    if existing_files or pdf_files:
        try:
            est_time = datetime.now().astimezone(timezone('US/Eastern'))
        except:
            est_time = datetime.utcnow()
        timestamp = est_time.strftime('%Y%m%d_%H%M%S')

        # Move existing markdown files
        for existing_file in existing_files:
            new_name = f"{filename}_{timestamp}{extension}"
            drive_service.files().update(
                fileId=existing_file['id'],
                addParents=old_folder_id,
                removeParents=main_folder_id,
                body={'name': new_name}
            ).execute()

        # Move matching PDF files
        for pdf_file in pdf_files:
            new_pdf_name = f"{filename}_{timestamp}.pdf"
            drive_service.files().update(
                fileId=pdf_file['id'],
                addParents=old_folder_id,
                removeParents=main_folder_id,
                body={'name': new_pdf_name}
            ).execute()

    # Upload new markdown file
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

        # Process the first/next file
        f = txt_files[0]
        file_id = f['id']
        filename = f['name']
        
        text_content = download_file_content(drive_service, file_id)
        md_content = call_openai_api(text_content, drive_service)
        base_name = re.sub(r'\.txt$', '', filename, flags=re.IGNORECASE)
        md_file_id = upload_markdown(drive_service, base_name, md_content)
        move_original_file(drive_service, file_id, filename, main_folder_id)

        remaining = len(txt_files) - 1
        if remaining > 0:
            return {
                'statusCode': 307,  # Temporary Redirect - indicates more work to do, in case there are more files to process
                'body': json.dumps({
                    'message': f'More files to process: {remaining} remaining',
                    'status': 'pending',
                    'processed': {
                        'original_txt': filename,
                        'md_file_id': md_file_id,
                        'status': 'success'
                    },
                    'remaining_files': remaining,
                    'timestamp': str(datetime.now())
                })
            }

        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'All text files processed',
                'status': 'success',
                'processed': {
                    'original_txt': filename,
                    'md_file_id': md_file_id,
                    'status': 'success'
                },
                'remaining_files': 0,
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
