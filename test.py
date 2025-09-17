import asyncio
from bleak import BleakClient, BleakScanner

# Replace with your printer's MAC address
PRINTER_ADDRESS = "66:32:30:73:CA:C8"

# Replace with your printer's WRITE characteristic UUID (you need to discover it)
WRITE_CHAR_UUID = "49535343-1e4d-4bd9-ba61-23c647249616"  # Example from your logs

# ESC/POS command to print "Hello from Pi" and a newline
ESC_POS_COMMANDS = b"Hello from Pi\n\x1B\x64\x02"  # \x1B\x64\x02 = feed 2 lines

async def run():
    # Optional: scan to confirm printer is nearby
    print("Scanning for BLE devices...")
    devices = await BleakScanner.discover(timeout=5.0)
    for d in devices:
        print(d)

    # Connect to the printer
    async with BleakClient(PRINTER_ADDRESS) as client:
        if not client.is_connected:
            print("Failed to connect to printer")
            return
        print(f"Connected to {PRINTER_ADDRESS}")

        # Write the ESC/POS command
        await client.write_gatt_char(WRITE_CHAR_UUID, ESC_POS_COMMANDS)
        print("Sent ESC/POS command to printer!")

# Run the async loop
asyncio.run(run())
