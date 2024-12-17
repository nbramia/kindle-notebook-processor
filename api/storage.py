import os
import json
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from io import BytesIO
from .utils import get_or_create_folder

def get_temp_folder(drive_service):
    """Get/create a temporary processing folder in Drive"""
    try:
        return get_or_create_folder(drive_service, "_temp_processing")
    except Exception as e:
        print(f"Error creating temp folder: {str(e)}")
        raise ValueError("Failed to create/access temporary storage folder")

def store_for_processing(drive_service, content, filename):
    """Store content temporarily in Drive"""
    try:
        temp_folder = get_temp_folder(drive_service)
        
        file_metadata = {
            'name': f'temp_{filename}',
            'parents': [temp_folder]
        }
        media = MediaIoBaseUpload(
            BytesIO(content.encode('utf-8')),
            mimetype='text/plain',
            resumable=True
        )
        file = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id'
        ).execute()
        return file.get('id')
    except Exception as e:
        print(f"Error storing file for processing: {str(e)}")
        raise ValueError(f"Failed to store file {filename} for processing") 