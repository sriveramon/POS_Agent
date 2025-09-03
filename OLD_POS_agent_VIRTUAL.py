import pika
import json
import requests
import sys
import os
from escpos.printer import Usb, Network

def get_access_token(base_url, password):
    url = f"{base_url}/auth/login"
    payload = {"password": password}
    try:
        resp = requests.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["access_token"]
    except Exception as e:
        print(f"Failed to login: {e}")
        sys.exit(1)

def fetch_printers(base_url, token):
    url = f"{base_url}/printers/"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = requests.get(url, headers=headers)
        resp.raise_for_status()
        printers = resp.json()
        # Build a dict with printer.name as key, and connection_data as value
        return {p["name"]: p["connection_data"] for p in printers}
    except Exception as e:
        print(f"Failed to fetch printers: {e}")
        sys.exit(1)

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

def print_receipt(text, printer_cfg, printer_id=None):
    try:
        print(f"Printing on (printer_id={printer_id}) with connection_data={printer_cfg}")
        print(f"{text}")
        # Here you can add logic to use escpos.printer according to printer_cfg
        # For demonstration, just printing to stdout
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
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
    except Exception as e:
        print(f"Error processing message: {e}")
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

def main():
    config = load_config()
    base_url = config["base_url"]
    password = config["password"]      # Add password to your config.json or set it in code
    RABBITMQ_URL = config["rabbitmq_url"]
    QUEUE_NAME = config["queue_name"]

    token = get_access_token(base_url, password)
    global PRINTERS
    PRINTERS = fetch_printers(base_url, token)
    print(f"Available printers: {PRINTERS}")

    params = pika.URLParameters(RABBITMQ_URL)
    connection = pika.BlockingConnection(params)
    channel = connection.channel()
    channel.queue_declare(queue=QUEUE_NAME, durable=True)
    print(f"Listening for print jobs on {QUEUE_NAME}...")
    channel.basic_consume(queue=QUEUE_NAME, on_message_callback=on_message)
    channel.start_consuming()

if __name__ == "__main__":
    main()