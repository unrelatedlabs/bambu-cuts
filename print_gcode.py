from io import BytesIO
import time
import zipfile
import bambulabs_api as bl
import os

IP = '192.168.1.150'
SERIAL = '0309CA480901636'
ACCESS_CODE = '31690006'

# ============================================================
INPUT_FILE_PATH = '/Users/pero/Downloads/a1_manual_bed_screws_adjust_assist.gcode'
UPLOAD_FILE_NAME = 'bambulab_api_example2.3mf'
# ============================================================

env = os.getenv("env", "debug")
plate = os.getenv("plate", "true").lower() == "true"




if __name__ == '__main__':
    print('Starting bambulabs_api example')
    print('Connecting to Bambulabs 3D printer')
    print(f'IP: {IP}')
    print(f'Serial: {SERIAL}')
    print(f'Access Code: {ACCESS_CODE}')

    # Create a new instance of the API
    printer = bl.Printer(IP, ACCESS_CODE, SERIAL)

    # Connect to the Bambulabs 3D printer
    printer.connect()

    time.sleep(5)

    file =  open("test.3mf", "rb")
    
    result = printer.upload_file(file, "test.gcode.3mf")

    if "226" not in result:
        print("Error Uploading File to Printer")
    else:
        print("Done Uploading/Sending Start Print Command")
        printer.start_print("test.gcode.3mf", 1)
        print("Start Print Command Sent")

    # if plate:
    #     gcode_location = "Metadata/plate_1.gcode"
    #     io_file = create_zip_archive_in_memory(gcode, gcode_location)
    #     if file:
    #         open("test.3mf", "wb").write(io_file.getvalue())
    #         result = printer.upload_file(io_file, UPLOAD_FILE_NAME)
    #         if "226" not in result:
    #             print("Error Uploading File to Printer")

    #         else:
    #             print("Done Uploading/Sending Start Print Command")
    #             printer.start_print(UPLOAD_FILE_NAME, 1)
    #             print("Start Print Command Sent")
    # else:
    #     gcode_location = INPUT_FILE_PATH
    #     io_file = create_zip_archive_in_memory(gcode, gcode_location)
    #     if file:
    #         open("test_no_plate.3mf", "wb").write(io_file.getvalue())
    #         result = printer.upload_file(io_file, UPLOAD_FILE_NAME)
    #         if "226" not in result:
    #             print("Error Uploading File to Printer")

    #         else:
    #             print("Done Uploading/Sending Start Print Command")
    #             printer.start_print(UPLOAD_FILE_NAME, gcode_location)
    #             print("Start Print Command Sent")

    
    time.sleep(5)


    while True:
    # Get the printer status
        status = printer.get_state()
        print(f'Printer status: {status}')
        time.sleep(1)
