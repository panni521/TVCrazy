import os
import csv
import requests
from datetime import datetime

CSV_FILES = [
    "txiptv.csv",
    "jsmpeg.csv",
    "zhgxtv.csv"
]

HISTORY_DIR = "history"
os.makedirs(HISTORY_DIR, exist_ok=True)

def fetch_csv(file_path):
    # You need to fill in your own download logic (e.g., API, scraping, etc.)
    # For demonstration, just read the local file.
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()

def save_history(file_path, content):
    basename = os.path.basename(file_path)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    hist_file = os.path.join(HISTORY_DIR, f"{basename}.{timestamp}.bak")
    with open(hist_file, "w", encoding="utf-8") as f:
        f.write(content)

def is_valid_csv(content):
    try:
        lines = content.splitlines()
        reader = csv.reader(lines)
        header = next(reader)
        row = next(reader)
        return bool(header) and bool(row)
    except Exception:
        return False

def update_csv(file_path):
    print(f"Processing: {file_path}")
    old_content = ""
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            old_content = f.read()
    # Fetch new content
    new_content = fetch_csv(file_path)
    if not is_valid_csv(new_content):
        print(f"Invalid CSV for {file_path}, skipping update.")
        return
    if new_content != old_content:
        print(f"Updating {file_path}, content changed.")
        save_history(file_path, old_content)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(new_content)
    else:
        print(f"No changes detected for {file_path}.")

def main():
    for file_path in CSV_FILES:
        if os.path.exists(file_path):
            update_csv(file_path)
        else:
            print(f"File not found: {file_path}")

if __name__ == "__main__":
    main()
