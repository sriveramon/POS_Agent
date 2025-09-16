import pika
import json
import requests
import sys
import os
import time
from escpos.impl.epson import GenericESCPOS
from escpos.ifusb import USBConnection
from escpos import NetworkConnection, BluetoothConnection
from urllib.parse import urlparse, urlunparse

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
        conn_data = printer_cfg.get("connection_data", {})

        # Setup connection
        if printer_type == "usb":
            vendor_id = int(conn_data["vendor_id"], 16)
            product_id = int(conn_data["product_id"], 16)
            interface = int(conn_data.get("interface", 0))
            ep_out = int(conn_data.get("ep_out", 3))
            ep_in = int(conn_data.get("ep_in", 0))
            conn = USBConnection.create(f"{vendor_id:04x}:{product_id:04x},interface={interface},ep_out={ep_out},ep_in={ep_in}")
        elif printer_type == "network":
            host = conn_data.get("ip_address") or conn_data.get("host")
            port = int(conn_data.get("port", 9100))
            conn = NetworkConnection.create(f"{host}:{port}")
        elif printer_type == "bluetooth":
            mac = conn_data.get("mac_address")
            if not mac:
                print("Bluetooth printer: no MAC address provided")
                return False
            conn = BluetoothConnection.create(mac)
        else:
            print(f"Unknown printer type: {printer_type}")
            return False

        # Initialize printer (generic Epson implementation)
        printer = GenericESCPOS(conn)
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
            print(f"Unknown printer_id: {printer_id}")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
    except Exception as e:
        print(f"Error processing message: {e}")
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

def start_rabbitmq_consumer(RABBITMQ_URL, QUEUE_NAME):
    parsed_url = urlparse(RABBITMQ_URL)
    safe_url = RABBITMQ_URL
    if parsed_url.password:
        safe_netloc = parsed_url.netloc.replace(parsed_url.password, "****")
        safe_url = urlunparse(parsed_url._replace(netloc=safe_netloc))
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
    print(f"Available printers: {list(PRINTERS.keys())}")

    RABBITMQ_URL, QUEUE_NAME = fetch_rabbitmq_info(BASE_URL, token)
    start_rabbitmq_consumer(RABBITMQ_URL, QUEUE_NAME)

if __name__ == "__main__":
    main()
