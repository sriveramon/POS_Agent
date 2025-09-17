import asyncio
from bleak import BleakClient

# Your printer's BLE address
PRINTER_ADDRESS = "66:32:30:73:CA:C8"

# The printer's write characteristic UUID (from your scan)
WRITE_CHAR_UUID = "49535343-1e4d-4bd9-ba61-23c647249616"

# ESC/POS command to print text and a new line
def escpos_text(text):
    return text.encode("utf-8") + b"\n"

async def print_ble():
    async with BleakClient(PRINTER_ADDRESS) as client:
        if not client.is_connected:
            print("Failed to connect to the printer")
            return
        
        print("Connected to printer")
        
        # Send the text
        await client.write_gatt_char(WRITE_CHAR_UUID, escpos_text("Hello from Raspberry Pi"))
        print("Text sent!")

        # Optional: Send ESC/POS cut command
        # await client.write_gatt_char(WRITE_CHAR_UUID, b'\x1d\x56\x00')  # Full cut

# Run the async function
asyncio.run(print_ble())
