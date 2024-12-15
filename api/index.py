# main.py
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

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

def get_gmail_service():
    """Initialize Gmail service using environment credentials."""
    try:
        # Expect pre-generated token from environment variables
        token_json = os.environ.get('GMAIL_TOKEN')
        if not token_json:
            raise ValueError("GMAIL_TOKEN environment variable not set")
        
        try:
            creds_dict = json.loads(token_json)
        except json.JSONDecodeError:
            raise ValueError("GMAIL_TOKEN environment variable contains invalid JSON")
            
        creds = Credentials.from_authorized_user_info(creds_dict, SCOPES)
        
        if not creds or not creds.valid:
            raise ValueError(
                "Invalid credentials. Generate new token.json locally and update GMAIL_TOKEN environment variable"
            )
            
        return build('gmail', 'v1', credentials=creds)
    except Exception as e:
        raise Exception(f"Failed to initialize Gmail service: {str(e)}")

def find_kindle_email(service):
    """Find the most recent Kindle email matching our pattern."""
    try:
        query = 'subject:"you sent a file" "from your kindle" newer_than:1d'
        result = service.users().messages().list(userId='me', q=query).execute()
        messages = result.get('messages', [])
        return messages[0]['id'] if messages else None
    except Exception as e:
        raise Exception(f"Error finding Kindle email: {str(e)}")

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

def download_pdf(url, filename):
    """Download and save the PDF."""
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        
        pdf_path = f"/tmp/{filename}.pdf"
        with open(pdf_path, 'wb') as f:
            f.write(response.content)
        return pdf_path
        
    except requests.exceptions.RequestException as e:
        raise Exception(f"Error downloading PDF: {str(e)}")

def handle_request(event, context):
    try:
        service = get_gmail_service()
        msg_id = find_kindle_email(service)
        
        if not msg_id:
            return {
                "statusCode": 200,
                "body": json.dumps({
                    "message": "No new Kindle emails found",
                    "timestamp": str(datetime.now())
                })
            }
            
        filename, html_body = extract_email_data(service, msg_id)
        pdf_url = extract_pdf_url(html_body)
        pdf_path = download_pdf(pdf_url, filename)
        
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Success",
                "filename": filename,
                "pdf_path": pdf_path,
                "timestamp": str(datetime.now())
            })
        }
    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps({
                "error": str(e),
                "timestamp": str(datetime.now())
            })
        }

def lambda_handler(event, context):
    return handle_request(event, context)

# Vercel handler
class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        result = handle_request(None, None)
        
        self.send_response(result.get("statusCode", 500))
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        
        response_body = result.get("body", json.dumps({"error": "Unknown error"}))
        self.wfile.write(response_body.encode())
        return

# For local testing
if __name__ == "__main__":
    print(json.dumps(handle_request(None, None), indent=2))