from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import json

SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/drive.file'
]

def test_token():
    """Test if the token is valid by making a simple API call."""
    try:
        # Load the token
        with open('token.json', 'r') as f:
            token_json = f.read()
        
        # Create credentials
        creds = Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)
        
        # Try to use it
        service = build('gmail', 'v1', credentials=creds)
        # Make a simple API call
        result = service.users().getProfile(userId='me').execute()
        
        print("✅ Token is valid!")
        print(f"Connected to Gmail account: {result.get('emailAddress')}")
        
    except Exception as e:
        print("❌ Token validation failed:")
        print(str(e))

if __name__ == "__main__":
    test_token()