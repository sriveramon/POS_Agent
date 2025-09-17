import asyncio
from bleak import BleakClient, BleakScanner

# Replace this with your printer's MAC address
PRINTER_MAC = "66:32:30:73:CA:C8"

# Example ESC/POS commands
def escpos_text(text):
    # Convert string to bytes with line feed
    return text.encode('utf-8') + b'\n'

def escpos_cut():
    # ESC/POS cut command
    return b'\x1dV\x01'

async def print_to_ble():
    # Scan for devices to make sure printer is available
    devices = await BleakScanner.discover()
    printer = None
    for d in devices:
        if d.address.upper() == PRINTER_MAC:
            printer = d
            break
    if not printer:
        print("Printer not found!")
        return

    async with BleakClient(PRINTER_MAC) as client:
        print(f"Connected to {PRINTER_MAC}")

        # Find writable characteristic (usually contains "write" or "tx")
        services = await client.get_services()
        write_char = None
        for service in services:
            for char in service.characteristics:
                if "write" in char.properties:
                    write_char = char
                    break
            if write_char:
                break

        if not write_char:
            print("No writable characteristic found!")
            return

        # Send text
        await client.write_gatt_char(write_char.uuid, escpos_text("Hello from Raspberry Pi BLE!"))
        await client.write_gatt_char(write_char.uuid, escpos_text("Printing ESC/POS via BLE"))
        await client.write_gatt_char(write_char.uuid, escpos_cut())

        print("Print job sent!")

# Run the async function
asyncio.run(print_to_ble())
