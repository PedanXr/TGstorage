import httpx
import asyncio
import os

# Configuration
API_BASE = "http://localhost:8082"  # Default port is 8082
API_KEY = "DEFAULT_INSECURE_KEY"    # Change this to your actual key

async def upload_file(file_path):
    if not os.path.exists(file_path):
        print(f"Error: File {file_path} not found")
        return

    async with httpx.AsyncClient(timeout=600) as client:
        with open(file_path, 'rb') as f:
            files = {'file': (os.path.basename(file_path), f)}
            headers = {'X-API-Key': API_KEY}
            
            print(f"Uploading {file_path}...")
            try:
                response = await client.post(f"{API_BASE}/upload", files=files, headers=headers)
                
                if response.status_code == 200:
                    data = response.json()
                    print("✓ Upload Success!")
                    print(f"File ID:     {data['file_id']}")
                    print(f"Direct Link: {data['direct_link']}")
                    print(f"Share Link:  {data['share_link']}")
                    return data
                else:
                    print(f"✗ Upload Failed: {response.status_code}")
                    print(response.text)
                    return None
            except Exception as e:
                print(f"✗ Request Error: {e}")

async def list_files(search=None):
    async with httpx.AsyncClient() as client:
        headers = {'X-API-Key': API_KEY}
        params = {"limit": 10}
        if search:
            params["search"] = search
            
        response = await client.get(f"{API_BASE}/files", headers=headers, params=params)
        if response.status_code == 200:
            files = response.json()
            print(f"\nFound {len(files)} files:")
            for f in files:
                print(f"- {f['file_name']} | Size: {f['file_size']} | Views: {f['view_count']}")
        else:
            print(f"Failed to list files: {response.status_code}")

async def main():
    # 1. List current files
    await list_files()
    
    # 2. Create a dummy file and upload it
    with open("example_file.txt", "w") as f:
        f.write("This is a test file for TG Storage Cluster")
    
    await upload_file("example_file.txt")
    
    # 3. List again to see the new file
    await list_files()
    
    # Cleanup
    if os.path.exists("example_file.txt"):
        os.remove("example_file.txt")

if __name__ == "__main__":
    asyncio.run(main())