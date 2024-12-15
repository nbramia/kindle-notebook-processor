Reads the GCP_SERVICE_ACCOUNT_JSON environment variable on Vercel (which you’ve set to the one-line JSON of your service account key).
Writes it to /tmp/gcp_creds.json and sets GOOGLE_APPLICATION_CREDENTIALS to that file, so Google Vision and Storage clients can authenticate.
Uses GCS_BUCKET environment variable for your Google Cloud Storage bucket name.
Processes unread Kindle emails, downloads PDFs, uploads them to Drive, performs OCR using Google Cloud Vision (PDF OCR via GCS), then creates and uploads a Markdown file alongside the PDF in Drive.

This should meet the requirements:

Reads credentials from environment variables.
Writes service account JSON to /tmp.
Uses Vision API via GCS for OCR.
Uploads PDF and Markdown to Drive.
Runs on Vercel with the environment variables set.


index.py handles email processing and starts OCR
cron.py periodically checks pending jobs
check-ocr.py handles OCR status checking and markdown creation/upload



Here's the flow:
index.py:
✅ Processes new Kindle emails
✅ Uploads PDFs to Drive
✅ Starts OCR process
✅ Adds jobs to tracking file in Drive
✅ Marks emails as read
check-ocr.py:
✅ Checks OCR status for a specific job
✅ Creates and uploads markdown when complete
✅ Returns appropriate status codes (202 for processing, 200 for complete)
cron.py:
✅ Uses shared utils functions
✅ Reads pending jobs from Drive
✅ Calls check-ocr endpoint for each job
✅ Updates jobs list in Drive
utils.py:
✅ Provides shared functions for job tracking
✅ Handles Drive file operations consistently