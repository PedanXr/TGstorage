#!/bin/bash

# Configuration
API_BASE="http://localhost:8082"
API_KEY="DEFAULT_INSECURE_KEY" # Change this to your actual key

echo "--- TG Storage API CLI Test ---"

# 1. Check Stats
echo -e "\n[1] Fetching Stats..."
curl -s -H "X-API-Key: $API_KEY" "$API_BASE/stats" | python3 -m json.tool || echo "Failed to fetch stats"

# 2. List Files
echo -e "\n[2] Listing Files..."
curl -s -H "X-API-Key: $API_KEY" "$API_BASE/files?limit=5" | python3 -m json.tool || echo "Failed to list files"

# 3. Upload File
echo "Hello TG Storage Cluster" > test_upload.txt
echo -e "\n[3] Uploading test_upload.txt..."
upload_res=$(curl -s -X POST -H "X-API-Key: $API_KEY" -F "file=@test_upload.txt" "$API_BASE/upload")
echo "$upload_res" | python3 -m json.tool

# 4. Cleanup test file
rm test_upload.txt