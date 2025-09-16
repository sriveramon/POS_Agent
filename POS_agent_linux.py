import pika
import json
import requests
import sys
import os
import time
from urllib.parse import urlparse, urlunparse

# PyESCPOS imports
from escpos.ifusb import USBConnection
from escpos import NetworkConnection, BluetoothConnection
from escpos.impl.epson import GenericESCPOS

# Globals for config and state
BASE_URL = None
PASSWORD = None
PRINTERS = None

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
        return {p["name"]: p for p in printers}
    except Exception as e:
        print(f"Failed to fetch printers: {e}")
        sys.exit(1)

def fetch_rabbitmq_info(base_url, token):
    url = f"{base_url}/auth/rabbitmq"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = requests.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return data["url"], data["queue_name"]
    except Exception as e:
        print(f"Failed to fetch RabbitMQ info: {e}")
        sys.exit(1)

def load_config(filename="config.json"):
    exe_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    path = os.path.join(exe_dir, filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"ERROR: '{filename}' not found in {exe_dir}")
        sys.exit(1)
    except Exception as e:
        print(f"Failed to load '{filename}': {e}")
        sys.exit(1)

def print_receipt(text, printer_cfg, printer_id=None):
    try:
        printer_type = printer_cfg.get("type")
        conn = printer_cfg.get("connection_data", {})

        if printer_type == "usb":
            # USBConnection expects a string like '20d1:7008,interface=0,ep_out=3,ep_in=0'
            usb_str = conn.get("usb_string")
            if not usb_str:
                print("USB printer: No usb_string provided.")
                return False
            connection = USBConnection.create(usb_str)
            printer = GenericESCPOS(connection)

        elif printer_type == "network":
            host = conn.get("ip_address") or conn.get("host")
            port = int(conn.get("port", 9100))
            connection = NetworkConnection.create(f"{host}:{port}")
            printer = GenericESCPOS(connection)

        elif printer_type == "bluetooth":
            mac_address = conn.get("mac_address")
            if not mac_address:
                print("Bluetooth printer: No mac_address provided.")
                return False
            # Optionally you can add port number after / if needed
            connection = BluetoothConnection.create(mac_address)
            printer = GenericESCPOS(connection)

        else:
            print(f"Unknown printer type: {printer_type}")
            return False

        printer.init()
        printer.text(text)
        printer.cut()
        print(f"Printed on printer '{printer_id}' with config: {printer_cfg}")
        return True
    except Exception as e:
        print(f"Error printing: {e}")
        return False

def on_message(ch, method, properties, body):
    global PRINTERS
    try:
        message = json.loads(body)
        msg_type = message.get("type")
        action = message.get("action")

        if msg_type == "printer" and action in ("update", "create", "delete"):
            print(f"Printer config change detected: {action}. Reloading printers...")
            token = get_access_token(BASE_URL, PASSWORD)
            PRINTERS = fetch_printers(BASE_URL, token)
            print(f"Reloaded printers: {list(PRINTERS.keys())}")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        printer_id = message.get("printer_id")
        printer_cfg = PRINTERS.get(printer_id)
        if printer_cfg:
            receipt_text = "\n".join(message.get("lines", []))
            success = print_receipt(receipt_text, printer_cfg, printer_id=printer_id)
            if success:
                ch.basic_ack(delivery_tag=method.delivery_tag)
            else:
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
        else:
            print(f"Unknown printer_id: {printer_id} -- Skipping")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
    except Exception as e:
        print(f"Error processing message: {e}")
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

def start_rabbitmq_consumer(RABBITMQ_URL, QUEUE_NAME):
    parsed_url = urlparse(RABBITMQ_URL)
    if parsed_url.password:
        safe_netloc = parsed_url.netloc.replace(parsed_url.password, "****")
        safe_url = urlunparse(parsed_url._replace(netloc=safe_netloc))
    else:
        safe_url = RABBITMQ_URL
    print(f"Using RabbitMQ URL: {safe_url}, Queue: {QUEUE_NAME}")

    while True:
        try:
            params = pika.URLParameters(RABBITMQ_URL)
            params.heartbeat = 30
            connection = pika.BlockingConnection(params)
            channel = connection.channel()
            channel.queue_declare(queue=QUEUE_NAME, durable=True)
            print(f"Listening for print jobs on {QUEUE_NAME}...")
            channel.basic_consume(queue=QUEUE_NAME, on_message_callback=on_message)
            channel.start_consuming()
        except pika.exceptions.AMQPConnectionError as e:
            print(f"RabbitMQ connection error: {e}. Retrying in 10 seconds...")
            time.sleep(10)
        except Exception as e:
            print(f"Unexpected error: {e}. Retrying in 10 seconds...")
            time.sleep(10)

def main():
    global BASE_URL, PASSWORD, PRINTERS
    config = load_config()
    BASE_URL = config["base_url"]
    PASSWORD = config["password"]

    token = get_access_token(BASE_URL, PASSWORD)
    PRINTERS = fetch_printers(BASE_URL, token)
    print(f"Available printers by name: {list(PRINTERS.keys())}")

    RABBITMQ_URL, QUEUE_NAME = fetch_rabbitmq_info(BASE_URL, token)

    start_rabbitmq_consumer(RABBITMQ_URL, QUEUE_NAME)

if __name__ == "__main__":
    main()
