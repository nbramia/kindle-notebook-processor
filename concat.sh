FILES=(
    "/Users/nathanramia/Documents/Code/Kindle/api/index.py"
    "/Users/nathanramia/Documents/Code/Kindle/vercel.json"
    "/Users/nathanramia/Documents/Code/Kindle/requirements.txt"
)
OUTPUT_FILE="/Users/nathanramia/Documents/Code/Kindle/concat.txt"

# Initial concatenation
cat "${FILES[@]}" > "$OUTPUT_FILE"
echo "Initial concatenation complete"

# Watch for changes
fswatch -0 "${FILES[@]}" | while read -d "" event
do
    cat "${FILES[@]}" > "$OUTPUT_FILE"
    echo "Updated at $(date) due to change in: $event"
done