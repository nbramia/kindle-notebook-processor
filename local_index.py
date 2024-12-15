import os
import json
import re
import requests
from base64 import urlsafe_b64decode
from urllib.parse import unquote
from bs4 import BeautifulSoup
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.http import MediaIoBaseUpload
from io import BytesIO
from datetime import datetime

# Add Google Drive scope
SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.modify',  # For marking emails as read
    'https://www.googleapis.com/auth/drive.file'     # For Google Drive access
]

def get_services():
    """Initialize Gmail and Drive services using local token."""
    try:
        creds = None
        if os.path.exists('token.json'):
            creds = Credentials.from_authorized_user_file('token.json', SCOPES)
        
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                raise ValueError("Invalid token. Please regenerate token.json with updated scopes")
        
        gmail_service = build('gmail', 'v1', credentials=creds)
        drive_service = build('drive', 'v3', credentials=creds)
        return gmail_service, drive_service
    except Exception as e:
        raise Exception(f"Failed to initialize services: {str(e)}")

def find_kindle_emails(service):
    """Find all unread Kindle emails matching our pattern."""
    try:
        query = 'subject:"you sent a file" "from your kindle" is:unread'
        result = service.users().messages().list(userId='me', q=query).execute()
        messages = result.get('messages', [])
        return messages if messages else []
    except Exception as e:
        raise Exception(f"Error finding Kindle emails: {str(e)}")

def mark_as_read(service, msg_id):
    """Mark an email as read by removing UNREAD label."""
    try:
        service.users().messages().modify(
            userId='me',
            id=msg_id,
            body={'removeLabelIds': ['UNREAD']}
        ).execute()
    except Exception as e:
        raise Exception(f"Error marking email as read: {str(e)}")

def extract_email_data(service, msg_id):
    """Extract subject, filename, and HTML body from the email."""
    try:
        email_data = service.users().messages().get(
            userId='me', id=msg_id, format='full').execute()
        
        # Get subject and extract filename
        headers = email_data['payload']['headers']
        subject = next(h['value'] for h in headers if h['name'].lower() == 'subject')
        filename_match = re.search(r'"([^"]+)"', subject)
        filename = filename_match.group(1) if filename_match else "kindle_download"
        
        # Extract HTML body
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

def extract_pdf_url(html_body):
    """Extract PDF download link using multiple methods."""
    if not html_body:
        raise ValueError("No HTML content in the email.")
    
    soup = BeautifulSoup(html_body, 'html.parser')
    pdf_url = None
    
    # Method 1: Direct link search
    for link in soup.find_all('a'):
        link_text = link.get_text().strip()
        if re.search(r'Download.*PDF', link_text, re.IGNORECASE):
            href = link.get('href')
            if href:
                if 'amazon.com/gp/f.html' in href:
                    encoded_url = re.search(r'&U=(.+?)&', href)
                    if encoded_url:
                        pdf_url = unquote(encoded_url.group(1))
                        break
                else:
                    pdf_url = href
                    break

    # Method 2: Search by string content if Method 1 fails
    if not pdf_url:
        for string in soup.stripped_strings:
            if re.search(r'Download.*PDF', string, re.IGNORECASE):
                parent_link = None
                for elem in soup.find_all(string=string):
                    if elem.find_parent('a'):
                        parent_link = elem.find_parent('a')
                        break
                if parent_link and parent_link.get('href'):
                    href = parent_link.get('href')
                    if 'amazon.com/gp/f.html' in href:
                        encoded_url = re.search(r'&U=(.+?)&', href)
                        if encoded_url:
                            pdf_url = unquote(encoded_url.group(1))
                            break
                    else:
                        pdf_url = href
                        break
    
    if not pdf_url:
        raise ValueError("No PDF download link found in the email body")
    
    return pdf_url

def get_or_create_folder(drive_service, folder_name="Kindle Notebooks"):
    """Get or create a folder in Google Drive."""
    try:
        # Search for existing folder
        response = drive_service.files().list(
            q=f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
            spaces='drive',
            fields='files(id, name)'
        ).execute()
        
        if response.get('files'):
            # Folder exists, return its ID
            return response['files'][0]['id']
        else:
            # Create folder
            file_metadata = {
                'name': folder_name,
                'mimeType': 'application/vnd.google-apps.folder'
            }
            file = drive_service.files().create(
                body=file_metadata,
                fields='id'
            ).execute()
            return file.get('id')
            
    except Exception as e:
        raise Exception(f"Error handling folder: {str(e)}")

def upload_to_drive(drive_service, file_content, filename):
    """Upload PDF to Google Drive in specific folder, overwriting if exists."""
    try:
        # Get or create the folder
        folder_id = get_or_create_folder(drive_service)
        
        # Search for existing file in the folder
        response = drive_service.files().list(
            q=f"name='{filename}.pdf' and '{folder_id}' in parents and trashed=false",
            spaces='drive',
            fields='files(id, name)'
        ).execute()
        
        # Prepare file metadata and media
        file_metadata = {
            'name': f'{filename}.pdf',
            'parents': [folder_id]  # This puts the file in our folder
        }
        media = MediaIoBaseUpload(
            BytesIO(file_content),
            mimetype='application/pdf',
            resumable=True
        )
        
        if response.get('files'):
            # Update existing file
            file_id = response['files'][0]['id']
            # Remove the parents field for update
            file_metadata.pop('parents', None)
            file = drive_service.files().update(
                fileId=file_id,
                body=file_metadata,
                media_body=media
            ).execute()
        else:
            # Create new file
            file = drive_service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id'
            ).execute()
        
        return file.get('id')
        
    except Exception as e:
        raise Exception(f"Error uploading to Drive: {str(e)}")
    
def process_kindle_emails():
    """Main function to process all unread Kindle emails."""
    try:
        print("Starting Kindle email processor...")
        
        # Initialize services
        gmail_service, drive_service = get_services()
        
        # Find all unread Kindle emails
        messages = find_kindle_emails(gmail_service)
        if not messages:
            print("No unread Kindle emails found")
            return
        
        processed_files = []
        
        # Process each email
        for message in messages:
            try:
                msg_id = message['id']
                filename, html_body = extract_email_data(gmail_service, msg_id)
                pdf_url = extract_pdf_url(html_body)
                
                # Download PDF
                response = requests.get(pdf_url, timeout=30)
                response.raise_for_status()
                pdf_content = response.content
                
                # Upload to Drive
                file_id = upload_to_drive(drive_service, pdf_content, filename)
                
                # Mark email as read
                mark_as_read(gmail_service, msg_id)
                
                processed_files.append({
                    'filename': filename,
                    'drive_file_id': file_id,
                    'status': 'success'
                })
                
                print(f"Processed: {filename}.pdf")
                
            except Exception as e:
                processed_files.append({
                    'message_id': msg_id,
                    'error': str(e),
                    'status': 'error'
                })
                print(f"Error processing email {msg_id}: {str(e)}")
        
        print(f"\nProcessing complete. {len(processed_files)} files processed.")
        return processed_files
        
    except Exception as e:
        print(f"Error: {str(e)}")
        return None

if __name__ == "__main__":
    process_kindle_emails()