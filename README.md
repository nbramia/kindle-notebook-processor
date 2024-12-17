# Kindle Scribe Notebook Processor

This project helps you automatically process Kindle Scribe notebook exports sent to your Gmail account into markdown files usable in notetaking apps (e.g. Obsidian).

## What It Does
When you share a notebook from your Kindle Scribe using the "Convert to text and email" option, this project:
1. Downloads PDFs and .TXT files from the Kindle-generated email
2. Archives the original email
3. Creates a "Kindle Notebooks" folder in your Google Drive
4. Uses GPT-4o to process the text into a structured markdown file based on locally-customizable prompt instructions (stored in the same folder as files themselves are saved).
5. Saves both original files and the processed markdown in Google Drive
6. Runs automatically every 10 minutes, via GitHub Actions

Everything necessary for this implementation is free - except the Kindle Scribe itself :)

If you're not sure where to start, copy this whole README into an LLM and ask it for help.

## Optional: Local Sync
You can set up local file mirroring to automatically sync the Kindle Notebooks folder from Google Drive to your local machine. This is useful if you want to edit the notes in a markdown-compatible notetaking app (e.g. Obsidian).
1. Use Google Drive for desktop
2. Configure it to sync (mirror) your "Kindle Notebooks" Drive folder to a local directory (e.g. your Obsidian vault)

If you do this, then each time you trigger an email to yourself from the Kindle Scribe, the notes you've taken will be downloaded, processed, synced to your local machine, and automagically appear as editable notes in your notetaking app.

## Prerequisites
1. GitHub account
2. Vercel account
3. Google Cloud account
4. OpenAI API key
5. Kindle Scribe 

## Setup Steps

### 1. Basic Setup
1. Clone this repository
2. Copy `.env.example` to `.env`

### 2. OAuth Setup
1. Create a new project in Google Cloud Console (https://console.cloud.google.com) using the email address you'll be using to share notebooks from your Kindle Scribe
2. Enable Gmail API and Google Drive API
3. Configure OAuth consent screen
   - User Type: External
   - Add yourself as a test user
   - Required scopes:
     - `https://www.googleapis.com/auth/gmail.readonly`
     - `https://www.googleapis.com/auth/gmail.modify`
     - `https://www.googleapis.com/auth/drive.file`
4. Create OAuth 2.0 Client ID credentials
   - Application type: Desktop app
   - Download as `credentials.json`
5. Run `python gmail_token_generator.py` to generate your `GMAIL_TOKEN`
6. Save the token output in .env (will also input as an environment variable in Vercel)

### 3. OpenAI Setup
1. Create an OpenAI API key (https://platform.openai.com/api-keys)
2. Save the key in .env (will also input as an environment variable in Vercel)


### 4. Vercel Setup

1. **Create Vercel Account**
   - Go to [vercel.com](https://vercel.com)
   - Sign up with your GitHub account

2. **Import GitHub Repository**
   - Click "Add New..."
   - Select "Project"
   - Choose your GitHub repository
   - Click "Import"

3. **Configure Project**
   - Framework Preset: Select "Other"
   - Build and Output Settings: Leave as default
   - Root Directory: Leave as `.` (root)
   - Click "Deploy"

4. **Add Environment Variables**
   - Go to Project Settings → Environment Variables
   - Add the following variables:     ```
     GMAIL_TOKEN=your_token_from_token_generator.py
     OPENAI_API_KEY=your_openai_api_key     ```
   - Make sure to add these to both Production and Preview environments

5. **Verify Deployment**
   - Go to Deployments tab
   - Check that build completed successfully
   - Test the API endpoints:
     - `your-project-url.vercel.app/api/index`
     - `your-project-url.vercel.app/api/distill_text`

6. **Set Up GitHub Integration** (for automatic deployments)
   - Already configured if you imported from GitHub
   - Vercel will automatically deploy when you push to main

7. **Troubleshooting**
   - Check Function Logs in Vercel Dashboard
   - Ensure environment variables are set correctly
   - Verify OAuth token hasn't expired (regenerate if needed)

Note: The free tier of Vercel has a 10-second timeout limit for serverless functions. This project is designed to work within this constraint by processing one file at a time.

### 5. GitHub Actions Setup
1. In GitHub, go to repository Settings → Secrets and variables → Actions
2. Add a new repository secret:
   - Name: `VERCEL_URL`
   - Value: Your Vercel project URL (without https://) - e.g. `kindle-notebook-processor-12345.vercel.app`
3. Your repository already contains the necessary workflow file; GitHub Actions will automatically run email processing and text processing every 10 minutes (on X:X0 and X:X1, respectively)

## Usage
1. On your Kindle Scribe, write your notes
2. Use the "Convert to text and email" sharing option
3. Send to your Gmail address
4. Wait up to 10 minutes for processing
5. Find your processed notes in Google Drive's "Kindle Notebooks" folder

## Architecture
- `/api/index.py`: Processes emails from Kindle received by Gmail
- `/api/distill_text.py`: Handles text processing and summarization
- GitHub Actions for automated processing

## Troubleshooting
1. **Email Not Processing**
   - Check Vercel function logs
   - Verify Gmail token hasn't expired
   - Ensure email is from Kindle Scribe

2. **Text Processing Errors**
   - Check OpenAI API key
   - Verify file permissions in Google Drive
   - Look for timeout errors in Vercel logs

3. **Token Expired**
   - Run `python token_generator.py` again
   - Update token in Vercel environment variables

## Contributing
See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License
MIT License - see [LICENSE](LICENSE)