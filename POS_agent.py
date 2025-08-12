import pika
import json
from escpos.printer import Usb, Network
import sys
import os

# Windows printer by name (requires pywin32)
import win32print

def load_config(filename="config.json"):
    exe_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    path = os.path.join(exe_dir, filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"ERROR: '{filename}' not found in {exe_dir}")
        print("Please make sure config.json is in the same directory as this program.")
        sys.exit(1)
    except Exception as e:
        print(f"Failed to load '{filename}': {e}")
        sys.exit(1)

config = load_config()
RABBITMQ_URL = config["rabbitmq_url"]
QUEUE_NAME = config["queue_name"]
PRINTERS = config["printers"]

def print_receipt(text, printer_cfg, printer_id=None):
    try:
        if printer_cfg["type"] == "usb":
            vendor_id = int(printer_cfg["vendor_id"], 16)
            product_id = int(printer_cfg["product_id"], 16)
            p = Usb(vendor_id, product_id)
            p.text(text)
            p.set()
            p.cut()
            p.close()
        elif printer_cfg["type"] == "network":
            p = Network(printer_cfg["host"], port=printer_cfg.get("port", 9100))
            p.text(text)
            p.set()
            p.cut()
            p.close()
        elif printer_cfg["type"] == "windows":
            
            printer_name = printer_cfg["name"]
            hPrinter = win32print.OpenPrinter(printer_name)
            try:
                hJob = win32print.StartDocPrinter(hPrinter, 1, ("Receipt", None, "RAW"))
                win32print.StartPagePrinter(hPrinter)
                win32print.WritePrinter(hPrinter, text.encode("utf-8"))
                win32print.EndPagePrinter(hPrinter)
                win32print.EndDocPrinter(hPrinter)
            finally:
                win32print.ClosePrinter(hPrinter)
        else:
            print(f"Unknown printer type: {printer_cfg['type']}")
            return False

        print(f"Printed on {printer_cfg}")
        return True
    except Exception as e:
        print(f"Error printing: {e}")
        return False

def on_message(ch, method, properties, body):
    try:
        message = json.loads(body)
        printer_id = message.get("printer_id")
        printer_cfg = PRINTERS.get(printer_id)
        if printer_cfg:
            receipt_text = "\n".join(message["lines"])
            success = print_receipt(receipt_text, printer_cfg, printer_id=printer_id)
            if success:
                print(f"Printed for printer_id: {printer_id}")
                ch.basic_ack(delivery_tag=method.delivery_tag)
            else:
                print(f"Print failed for printer_id: {printer_id}")
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
        else:
            print(f"Unknown printer_id: {printer_id} -- Skipping")
            ch.basic_ack(delivery_tag=method.delivery_tag)
    except Exception as e:
        print(f"Error processing message: {e}")
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

def main():
    params = pika.URLParameters(RABBITMQ_URL)
    connection = pika.BlockingConnection(params)
    channel = connection.channel()
    channel.queue_declare(queue=QUEUE_NAME, durable=True)
    print(f"Listening for print jobs on {QUEUE_NAME}...")
    channel.basic_consume(queue=QUEUE_NAME, on_message_callback=on_message)
    channel.start_consuming()

if __name__ == "__main__":
    main()
