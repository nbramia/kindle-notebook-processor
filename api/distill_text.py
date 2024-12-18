# distill_text.py
#
# This script processes text files from Kindle notebooks:
# 1. Finds .txt files in the "Kindle Notebooks" Google Drive folder
# 2. Processes one file at a time (to stay within Vercel's 10s limit)
# 3. Uses OpenAI's GPT-4o-mini to create a structured markdown summary
# 4. Saves the markdown and moves original files to an "Old" subfolder
#
# The script is designed to be called via Vercel's serverless functions
# and is triggered by GitHub Actions every 10 minutes.

import os
import re
import json
import time
from datetime import datetime
from pytz import timezone
from io import BytesIO
from http.server import BaseHTTPRequestHandler
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from googleapiclient.discovery import build
from openai import OpenAI
from .storage import store_for_processing
from .utils import get_services, get_or_create_folder

def list_txt_files(drive_service):
    """List all .txt files in Kindle Notebooks folder.
    
    Finds or creates the main "Kindle Notebooks" folder and lists
    all text files within it that haven't been processed yet.
    
    Returns:
        tuple: (list of file objects, main folder ID)
        Each file object contains id, name, and modifiedTime
    """
    # Get or create Kindle Notebooks folder
    main_folder_id = get_or_create_folder(drive_service, "Kindle Notebooks")

    query = f"mimeType='text/plain' and '{main_folder_id}' in parents and trashed=false"
    print(f"Searching for files with query: {query}")
    
    response = drive_service.files().list(
        q=query,
        spaces='drive',
        fields='files(id, name, mimeType, parents)' 
    ).execute()
    
    files = response.get('files', [])
    print(f"Found {len(files)} files:")
    for f in files:
        print(f"- {f['name']} (ID: {f['id']}, Type: {f['mimeType']})")
    
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
    """Get or create the prompt instructions file in Drive.
    
    Looks for 'prompt_instructions.md' in the Kindle Notebooks folder.
    If not found, creates it with default instructions for GPT-4o-mini.
    You can edit this file to customize the prompt for your needs. 
    If you do, your instructions will be used instead of the default ones.
    Includes retry logic to handle potential race conditions.
    
    Returns:
        str: Content of the prompt file
    """
    max_retries = 3
    retry_delay = 2  # seconds

    main_folder_id = get_or_create_folder(drive_service, "Kindle Notebooks")
    
    # Look for the prompt file in Drive, which a user may edit to customize the prompt used to process the text into notes. 
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
            "2. Include the date in the summary, if found in the notes. Use extremely concise language in the Summary section - no fluff, no extra words - and use bullet points if appropriate. Less than 100 words total.\n"
            "3. Extract any action items or to-dos. Only include items that are specifically called out directly as followups - don't include items that are indirectly implied as things that would be nice, don't try too hard to infer. Something being identified as an issue does not mean it's an action item. If you find no action items, that's fine, just say 'No action items found'.\n"
            "4. The 'Handwritten Notes' section should be exactly equivalent to ALL of the original text you were sent to process. Maintain organization by nesting levels of bullet points / headers as appropriate to reflect the original structure of the notes. You may try to correct obvious errors in OCR - don't get creative, but if you see a word that almost certainly should have been something else given the context, you may correct it.\n"
            "5. The 'Insights' section is optional. IF you have any key insights, links to relevant articles, or other information that would likely be useful to the person who took these notes, you may include this section.\n"
            "Output the result in Markdown format, leveraging # to create section headers, hyphens (followed by a space!) to create bullets, '- [ ]' to create checkboxes, and numerical lists. Do not start and end the .md file with '```markdown```' - simply display the formatted text itself. No tick marks. I'm going to open the resulting file in a markdown editor like Obsidian and I want it to be formatted nicely. Organize into following sections:\n"
            "### Summary\n\n"
            "### Action Items\n\n"
            "### Handwritten Notes\n\n"
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
    """Process text content using OpenAI's GPT-4o-mini.
    
    Gets prompt from Drive, combines with input text, and calls GPT-4o-mini
    to generate a structured markdown summary with:
    - Summary (< 100 words)
    - Action Items
    - Original Notes (cleaned)
    - Optional Insights
    
    Args:
        text: Raw text content to process
        drive_service: For fetching custom prompt if exists
    
    Returns:
        str: Formatted markdown content
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable not set.")
    
    client = OpenAI(api_key=api_key)
    
    # Get prompt from Drive
    prompt = get_prompt_from_drive(drive_service)
    prompt += "\n\nText to process:\n" + text

    response = client.chat.completions.create(
        model="gpt-4o-mini",
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
        temperature=0.3 # not too creative
    )

    return response.choices[0].message.content.strip()

def upload_markdown(drive_service, filename, content):
    """Upload markdown file and archive any existing versions.
    
    If a file with the same name exists:
    1. Moves existing .md file to Old folder with timestamp
    2. Moves matching .pdf to Old folder with same timestamp
    3. Creates new .md file in main folder
    
    Returns:
        str: ID of newly created markdown file
    """
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
    """Archive original .txt file with timestamp.
    
    Moves the processed text file to the Old folder and
    renames it to include the processing timestamp.
    """
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
    """Step 1: Download and queue text files for processing.
    
    This function:
    1. Initializes Drive service
    2. Lists all .txt files in Kindle Notebooks folder
    3. Takes the first unprocessed file
    4. Downloads its content
    5. Stores it in a temporary processing folder
    
    Returns:
        dict: Response with:
            - statusCode: 200 for success, 500 for errors
            - body: JSON with:
                - status: 'queued' or 'no_files'
                - temp_id: ID of temporary stored file
                - original_file: Name of file being processed
                - original_id: ID of original file
                
    Note: This is step 1 of 3 in the processing pipeline, designed
    to stay within Vercel's 10-second execution limit.
    """
    try:
        print("Starting Step 1: Download and queue")
        drive_service = get_services()
        print("Services initialized")
        
        txt_files, main_folder_id = list_txt_files(drive_service)
        print(f"Found {len(txt_files)} files to process")

        if not txt_files:
            return {'statusCode': 200, 'body': json.dumps({'status': 'no_files'})}

        # Process first file
        f = txt_files[0]
        print(f"Processing file: {f['name']}")
        content = download_file_content(drive_service, f['id'])
        print("File content downloaded")
        
        # Store in temp folder
        temp_id = store_for_processing(drive_service, content, f['name'])
        print(f"File queued with temp_id: {temp_id}")
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'status': 'queued',
                'temp_id': temp_id,
                'original_file': f['name'],
                'original_id': f['id']
            })
        }
    except Exception as e:
        print(f"Error in process_text_files: {str(e)}")
        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        result = process_text_files()

        self.send_response(result.get('statusCode', 500))
        self.send_header('Content-type', 'application/json')
        self.end_headers()

        response_body = result.get('body', json.dumps({'error': 'Unknown error'}))
        self.wfile.write(response_body.encode())
        return
