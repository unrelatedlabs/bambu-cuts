#!/usr/bin/env python3
"""
Script to compress a 3mf folder into a 3mf file.
Updates MD5 files before compressing.
"""

import os
import hashlib
import zipfile
import argparse
from pathlib import Path


def calculate_md5(file_path):
    """Calculate MD5 hash of a file."""
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest().upper()


def update_md5_files(folder_path):
    """Update all MD5 files in the folder with current file hashes."""
    folder = Path(folder_path)
    updated_files = []
    
    # Find all .md5 files
    for md5_file in folder.rglob("*.md5"):
        # Get the corresponding file (remove .md5 extension)
        target_file = md5_file.with_suffix("")
        
        if target_file.exists():
            # Calculate new MD5 hash
            new_hash = calculate_md5(target_file)
            
            # Write the new hash to the MD5 file
            with open(md5_file, 'w') as f:
                f.write(new_hash)
            
            updated_files.append(str(md5_file))
            print(f"Updated MD5 for {target_file.name}: {new_hash}")
        else:
            print(f"Warning: Target file {target_file} not found for MD5 file {md5_file}")
    
    return updated_files


def compress_3mf_folder(folder_path, output_path):
    """Compress a 3mf folder into a 3mf file."""
    folder = Path(folder_path)
    
    if not folder.exists():
        raise FileNotFoundError(f"Folder {folder_path} does not exist")
    
    # Create the output directory if it doesn't exist
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        # Walk through all files in the folder
        for file_path in folder.rglob("*"):
            if file_path.is_file():
                # Get relative path from the folder root
                arcname = file_path.relative_to(folder)
                zipf.write(file_path, arcname)
                print(f"Added to archive: {arcname}")
    
    print(f"Successfully created 3mf file: {output_path}")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Compress a 3mf folder into a 3mf file")
    parser.add_argument("folder_path", help="Path to the 3mf folder to compress")
    parser.add_argument("-o", "--output", help="Output 3mf file path", 
                       default=None)
    parser.add_argument("--no-md5-update", action="store_true", 
                       help="Skip updating MD5 files")
    
    args = parser.parse_args()
    
    folder_path = Path(args.folder_path)
    
    if not folder_path.exists():
        print(f"Error: Folder {folder_path} does not exist")
        return 1
    
    if not folder_path.is_dir():
        print(f"Error: {folder_path} is not a directory")
        return 1
    
    # Set default output path if not provided
    if args.output is None:
        output_path = folder_path.with_suffix('.3mf')
    else:
        output_path = Path(args.output)
    
    try:
        # Update MD5 files if not disabled
        if not args.no_md5_update:
            print("Updating MD5 files...")
            updated_files = update_md5_files(folder_path)
            if updated_files:
                print(f"Updated {len(updated_files)} MD5 files")
            else:
                print("No MD5 files found to update")
        else:
            print("Skipping MD5 file updates")
        
        # Compress the folder
        print(f"\nCompressing folder {folder_path} to {output_path}...")
        compress_3mf_folder(folder_path, output_path)
        
        print("\nCompression completed successfully!")
        return 0
        
    except Exception as e:
        print(f"Error: {e}")
        return 1


if __name__ == "__main__":
    exit(main())


