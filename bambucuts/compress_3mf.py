#!/usr/bin/env python3
"""
3MF File Processor Module

A module for processing 3MF files and folders, updating MD5 files, and inserting gcode.
Can be used both as a command-line tool and imported as a library.

Usage as library:
    from compress_3mf import process_3mf
    
    # Simple usage: template 3mf + gcode -> output 3mf
    process_3mf('template.3mf', 'output.3mf', gcode_file='my_gcode.gcode')
    
    # Or use the class for more control
    processor = ThreeMFProcessor()
    processor.process_file('template.3mf', 'output.3mf', gcode_file='my_gcode.gcode')

Usage as CLI:
    python compress_3mf.py template.3mf -o output.3mf -g my_gcode.gcode
"""

import os
import hashlib
import zipfile
import argparse
from pathlib import Path
from io import BytesIO
from typing import Optional, Union


class ThreeMFProcessor:
    """A class for processing 3MF files and folders."""
    
    def __init__(self, verbose: bool = True):
        """Initialize the processor.
        
        Args:
            verbose: Whether to print progress messages
        """
        self.verbose = verbose
    
    def _log(self, message: str) -> None:
        """Print a message if verbose mode is enabled."""
        if self.verbose:
            print(message)
    
    def _calculate_md5_file(self, file_path: Union[str, Path]) -> str:
        """Calculate MD5 hash of a file."""
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest().upper()
    
    def _calculate_md5_bytes(self, data: bytes) -> str:
        """Calculate MD5 hash of bytes data."""
        hash_md5 = hashlib.md5()
        hash_md5.update(data)
        return hash_md5.hexdigest().upper()
    
    def _insert_gcode_into_plate_content(self, plate_content: str, gcode_content: str) -> str:
        """Insert gcode content into plate gcode content between PLOT START and PLOT END markers."""
        # Find the PLOT START and PLOT END markers
        plot_start_marker = "; PLOT START"
        plot_end_marker = "; PLOT END"
        
        start_pos = plate_content.find(plot_start_marker)
        end_pos = plate_content.find(plot_end_marker)
        
        if start_pos == -1:
            raise ValueError(f"Could not find '{plot_start_marker}' marker in plate content")
        
        if end_pos == -1:
            raise ValueError(f"Could not find '{plot_end_marker}' marker in plate content")
        
        # Find the end of the PLOT START line
        start_line_end = plate_content.find('\n', start_pos)
        if start_line_end == -1:
            start_line_end = len(plate_content)
        else:
            start_line_end += 1  # Include the newline
        
        # Insert the gcode content between the markers
        new_content = (plate_content[:start_line_end] + 
                       gcode_content + 
                       '\n' + 
                       plate_content[end_pos:])
        
        return new_content
    
    def _update_md5_files_folder(self, folder_path: Path) -> list:
        """Update all MD5 files in the folder with current file hashes."""
        updated_files = []
        
        # Find all .md5 files
        for md5_file in folder_path.rglob("*.md5"):
            # Get the corresponding file (remove .md5 extension)
            target_file = md5_file.with_suffix("")
            
            if target_file.exists():
                # Calculate new MD5 hash
                new_hash = self._calculate_md5_file(target_file)
                
                # Write the new hash to the MD5 file
                with open(md5_file, 'w') as f:
                    f.write(new_hash)
                
                updated_files.append(str(md5_file))
                self._log(f"Updated MD5 for {target_file.name}: {new_hash}")
            else:
                self._log(f"Warning: Target file {target_file} not found for MD5 file {md5_file}")
        
        return updated_files
    
    def _insert_gcode_into_plate_file(self, plate_gcode_path: Path, gcode_file_path: Path) -> None:
        """Insert gcode file content into plate_1.gcode between PLOT START and PLOT END markers."""
        if not plate_gcode_path.exists():
            raise FileNotFoundError(f"Plate gcode file {plate_gcode_path} does not exist")
        
        if not gcode_file_path.exists():
            raise FileNotFoundError(f"Gcode file {gcode_file_path} does not exist")
        
        # Read the plate gcode file
        with open(plate_gcode_path, 'r') as f:
            plate_content = f.read()
        
        # Read the gcode file to insert
        with open(gcode_file_path, 'r') as f:
            gcode_content = f.read()
        
        # Insert the gcode content
        new_content = self._insert_gcode_into_plate_content(plate_content, gcode_content)
        
        # Write the modified content back to the file
        with open(plate_gcode_path, 'w') as f:
            f.write(new_content)
        
        self._log(f"Inserted gcode from {gcode_file_path.name} into {plate_gcode_path.name}")
    
    def _compress_folder(self, folder_path: Path, output_path: Path) -> None:
        """Compress a 3mf folder into a 3mf file."""
        if not folder_path.exists():
            raise FileNotFoundError(f"Folder {folder_path} does not exist")
        
        # Create the output directory if it doesn't exist
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # Walk through all files in the folder
            for file_path in folder_path.rglob("*"):
                if file_path.is_file():
                    # Get relative path from the folder root
                    arcname = file_path.relative_to(folder_path)
                    zipf.write(file_path, arcname)
                    self._log(f"Added to archive: {arcname}")
        
        self._log(f"Successfully created 3mf file: {output_path}")
    
    def _process_3mf_file_in_memory(self, input_path: Path, output_path: Path, gcode_file_path: Optional[Path] = None) -> None:
        """Process a 3MF file in memory, updating MD5 files and optionally inserting gcode."""
        if not input_path.exists():
            raise FileNotFoundError(f"3MF file {input_path} does not exist")
        
        if not input_path.suffix.lower() == '.3mf':
            raise ValueError(f"File {input_path} is not a 3MF file")
        
        # Create the output directory if it doesn't exist
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Read the input 3MF file into memory
        with zipfile.ZipFile(input_path, 'r') as input_zip:
            # Collect all files and their content
            file_contents = {}
            for file_info in input_zip.infolist():
                content = input_zip.read(file_info.filename)
                file_contents[file_info.filename] = (file_info, content)
                self._log(f"Added to archive: {file_info.filename}")
            
            # Insert gcode if provided
            if gcode_file_path:
                if not gcode_file_path.exists():
                    raise FileNotFoundError(f"Gcode file {gcode_file_path} does not exist")
                
                # Read the gcode file
                with open(gcode_file_path, 'r') as f:
                    gcode_content = f.read()
                
                # Find plate_1.gcode in the ZIP
                plate_gcode_file = "Metadata/plate_1.gcode"
                if plate_gcode_file in file_contents:
                    # Read the current plate gcode content
                    plate_content = file_contents[plate_gcode_file][1].decode('utf-8')
                    
                    # Insert the gcode content
                    new_plate_content = self._insert_gcode_into_plate_content(plate_content, gcode_content)
                    
                    # Update the plate gcode file content
                    file_info, _ = file_contents[plate_gcode_file]
                    file_contents[plate_gcode_file] = (file_info, new_plate_content.encode('utf-8'))
                    self._log(f"Inserted gcode from {gcode_file_path.name} into {plate_gcode_file}")
                else:
                    raise FileNotFoundError(f"Could not find {plate_gcode_file} in 3MF file")
            
            # Update MD5 files
            self._log("Updating MD5 files...")
            updated_files = []
            for filename, (file_info, content) in file_contents.items():
                if filename.endswith('.md5'):
                    # Get the corresponding file (remove .md5 extension)
                    target_file = filename[:-4]
                    
                    if target_file in file_contents:
                        # Calculate new MD5 hash
                        target_content = file_contents[target_file][1]
                        new_hash = self._calculate_md5_bytes(target_content)
                        
                        # Update the MD5 file content
                        file_contents[filename] = (file_info, new_hash.encode('utf-8'))
                        updated_files.append(filename)
                        self._log(f"Updated MD5 for {target_file}: {new_hash}")
            
            if updated_files:
                self._log(f"Updated {len(updated_files)} MD5 files")
            else:
                self._log("No MD5 files found to update")
            
            # Create the output ZIP file
            with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as output_zip:
                for filename, (file_info, content) in file_contents.items():
                    output_zip.writestr(file_info, content)
        
        self._log(f"Successfully created 3mf file: {output_path}")
    
    def process_file(self, input_path: Union[str, Path], output_path: Union[str, Path], 
                    gcode_file: Optional[Union[str, Path]] = None) -> None:
        """Process a 3MF file or folder.
        
        Args:
            input_path: Path to the 3MF file or folder to process
            output_path: Path for the output 3MF file
            gcode_file: Optional path to gcode file to insert into plate_1.gcode
        """
        input_path = Path(input_path)
        output_path = Path(output_path)
        gcode_file_path = Path(gcode_file) if gcode_file else None
        
        if not input_path.exists():
            raise FileNotFoundError(f"Input path {input_path} does not exist")
        
        # Determine if input is a 3MF file or folder
        if input_path.is_file() and input_path.suffix.lower() == '.3mf':
            self._log(f"Input is a 3MF file: {input_path}")
            # Process 3MF file in memory
            self._process_3mf_file_in_memory(input_path, output_path, gcode_file_path)
        elif input_path.is_dir():
            self._log(f"Input is a folder: {input_path}")
            
            # Insert gcode file if provided
            if gcode_file_path:
                if not gcode_file_path.exists():
                    raise FileNotFoundError(f"Gcode file {gcode_file_path} does not exist")
                
                # Look for plate_1.gcode in the folder
                plate_gcode_path = input_path / "Metadata" / "plate_1.gcode"
                if not plate_gcode_path.exists():
                    raise FileNotFoundError(f"Could not find plate_1.gcode at {plate_gcode_path}")
                
                self._log(f"Inserting gcode from {gcode_file_path.name} into plate_1.gcode...")
                self._insert_gcode_into_plate_file(plate_gcode_path, gcode_file_path)
            
            # Update MD5 files
            self._log("Updating MD5 files...")
            updated_files = self._update_md5_files_folder(input_path)
            if updated_files:
                self._log(f"Updated {len(updated_files)} MD5 files")
            else:
                self._log("No MD5 files found to update")
            
            # Compress the folder
            self._log(f"Compressing folder {input_path} to {output_path}...")
            self._compress_folder(input_path, output_path)
        else:
            raise ValueError(f"{input_path} is neither a 3MF file nor a directory")


# Convenience functions for simple usage
def process_3mf(input_path: Union[str, Path], output_path: Union[str, Path], 
                gcode_file: Optional[Union[str, Path]] = None, verbose: bool = True) -> None:
    """Process a 3MF file or folder with a simple function interface.
    
    Args:
        input_path: Path to the 3MF file or folder to process
        output_path: Path for the output 3MF file
        gcode_file: Optional path to gcode file to insert into plate_1.gcode
        verbose: Whether to print progress messages
    """
    processor = ThreeMFProcessor(verbose=verbose)
    processor.process_file(input_path, output_path, gcode_file)


# Legacy function wrappers for backward compatibility
def calculate_md5(file_path):
    """Calculate MD5 hash of a file. (Legacy function)"""
    processor = ThreeMFProcessor(verbose=False)
    return processor._calculate_md5_file(file_path)


def calculate_md5_from_bytes(data):
    """Calculate MD5 hash of bytes data. (Legacy function)"""
    processor = ThreeMFProcessor(verbose=False)
    return processor._calculate_md5_bytes(data)


# Legacy function wrappers for backward compatibility
def insert_gcode_into_plate_content(plate_content, gcode_content):
    """Insert gcode content into plate gcode content between PLOT START and PLOT END markers. (Legacy function)"""
    processor = ThreeMFProcessor(verbose=False)
    return processor._insert_gcode_into_plate_content(plate_content, gcode_content)


def insert_gcode_into_plate(plate_gcode_path, gcode_file_path):
    """Insert gcode file content into plate_1.gcode between PLOT START and PLOT END markers. (Legacy function)"""
    processor = ThreeMFProcessor(verbose=True)
    processor._insert_gcode_into_plate_file(Path(plate_gcode_path), Path(gcode_file_path))
    return Path(plate_gcode_path)


def update_md5_files(folder_path):
    """Update all MD5 files in the folder with current file hashes. (Legacy function)"""
    processor = ThreeMFProcessor(verbose=True)
    return processor._update_md5_files_folder(Path(folder_path))


def compress_3mf_folder(folder_path, output_path):
    """Compress a 3mf folder into a 3mf file. (Legacy function)"""
    processor = ThreeMFProcessor(verbose=True)
    processor._compress_folder(Path(folder_path), Path(output_path))
    return Path(output_path)


def main():
    """Command-line interface for the 3MF processor."""
    parser = argparse.ArgumentParser(description="Compress a 3mf folder or 3mf file into a 3mf file")
    parser.add_argument("input_path", help="Path to the 3mf folder or 3mf file to compress")
    parser.add_argument("-o", "--output", help="Output 3mf file path", 
                       default=None)
    parser.add_argument("-g", "--gcode", help="Gcode file to insert into plate_1.gcode between PLOT START and PLOT END markers")
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress progress messages")
    
    args = parser.parse_args()
    
    input_path = Path(args.input_path)
    
    if not input_path.exists():
        print(f"Error: Input path {input_path} does not exist")
        return 1
    
    # Set default output path if not provided
    if args.output is None:
        if input_path.suffix.lower() == '.3mf':
            # If input was a 3MF file, use the same name for output
            output_path = input_path
        else:
            # If input was a folder, add .3mf extension
            output_path = input_path.with_suffix('.3mf')
    else:
        output_path = Path(args.output)
    
    try:
        # Use the new class-based API
        processor = ThreeMFProcessor(verbose=not args.quiet)
        processor.process_file(input_path, output_path, args.gcode)
        
        if not args.quiet:
            print("\nCompression completed successfully!")
        return 0
        
    except Exception as e:
        print(f"Error: {e}")
        return 1


if __name__ == "__main__":
    exit(main())


