#!/usr/bin/env python3
"""
Simple test script based on the user's example
Tests the RTSP connection with authentication
"""

import cv2
from config import PRINTER_IP, ACCESS_CODE

def test_rtsp_connection():
    """Test RTSP connection using the exact method from user's example"""
    
    # Use config.py values
    printer_ip = PRINTER_IP
    access_code = ACCESS_CODE
    
    print(f"Testing RTSP connection to {printer_ip}")
    print(f"Using access code: {access_code}")
    print("-" * 50)
    
    # RTSP URL format (works if your firmware still allows it)
    url = f"rtsps://bblp:{access_code}@{printer_ip}/streaming/live/1"
    
    print(f"Connecting to: {url}")
    
    # Open stream
    cap = cv2.VideoCapture(url)
    
    if not cap.isOpened():
        print("❌ Failed to open stream. Check if LAN mode live view is enabled.")
        print("Trying alternative URLs...")
        
        # Try alternative URLs
        alt_urls = [
            f"rtsp://bblp:{access_code}@{printer_ip}/streaming/live/1",
            f"rtsp://{printer_ip}/streaming/live/1",
        ]
        
        for alt_url in alt_urls:
            print(f"Trying: {alt_url}")
            cap = cv2.VideoCapture(alt_url)
            if cap.isOpened():
                print(f"✓ Connected to: {alt_url}")
                url = alt_url
                break
            cap.release()
    
    if not cap.isOpened():
        print("❌ All connection attempts failed.")
        return False
    
    print("✓ Stream opened successfully!")
    print("Press 'q' to quit, 's' to save frame")
    
    frame_count = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            print("❌ Failed to grab frame")
            break
        
        frame_count += 1
        if frame_count % 30 == 0:  # Print status every 30 frames
            print(f"Frames received: {frame_count}")
        
        cv2.imshow("Bambu A1 Camera", frame)
        
        # Press 'q' to quit, 's' to save
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord('s'):
            # Save current frame
            import datetime
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"test_frame_{timestamp}.jpg"
            cv2.imwrite(filename, frame)
            print(f"✓ Frame saved as: {filename}")
    
    cap.release()
    cv2.destroyAllWindows()
    print(f"Test completed. Total frames: {frame_count}")
    return True

if __name__ == "__main__":
    test_rtsp_connection()


