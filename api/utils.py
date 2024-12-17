# utils.py
#
# Shared utility functions for Google Drive operations
# Currently includes folder management functions used by
# multiple modules in the processing pipeline.

import os
import json
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from google.auth.transport.requests import Request

# Add the get_services function here since it's used by multiple modules
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
    """Get or create a folder by name in Google Drive."""
    try:
        # Escape single quotes in folder name for query
        safe_folder_name = folder_name.replace("'", "\\'")
        
        query = f"mimeType='application/vnd.google-apps.folder' and trashed=false"
        if parent_id:
            query += f" and '{parent_id}' in parents"
        query += f" and name='{safe_folder_name}'"
        
        print(f"Searching for folder: {folder_name}")
        results = drive_service.files().list(
            q=query,
            spaces='drive',
            fields='files(id, name)'
        ).execute()
        
        folders = results.get('files', [])
        
        if folders:
            print(f"Found existing folder: {folder_name}")
            return folders[0]['id']
            
        # Folder doesn't exist, create it
        print(f"Creating new folder: {folder_name}")
        folder_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        if parent_id:
            folder_metadata['parents'] = [parent_id]
            
        folder = drive_service.files().create(
            body=folder_metadata,
            fields='id'
        ).execute()
        
        folder_id = folder.get('id')
        if not folder_id:
            raise ValueError(f"Failed to get ID for newly created folder: {folder_name}")
            
        print(f"Successfully created folder: {folder_name}")
        return folder_id
        
    except Exception as e:
        print(f"Error in get_or_create_folder: {str(e)}")
        raise ValueError(f"Failed to get/create folder '{folder_name}': {str(e)}") 