from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/drive.file'
]

def generate_token():
    """Generate token with offline access."""
    flow = InstalledAppFlow.from_client_secrets_file(
        'credentials.json', 
        SCOPES
    )
    
    # Run the OAuth flow with offline access
    creds = flow.run_local_server(
        port=8099,
        access_type='offline',
        prompt='consent'
    )
    
    # Save the credentials
    with open('token.json', 'w') as token:
        token.write(creds.to_json())
    
    print("\nYour GMAIL_TOKEN for Vercel:\n")
    print(creds.to_json())
    print("\nToken also saved to token.json")

if __name__ == "__main__":
    generate_token()