import pika
import json
import requests
import sys
import os
import time
import subprocess
from escpos.printer import Usb, Network, Serial

# Try to import Bluetooth printer class if available
try:
    from escpos.printer import Bluetooth
    BLUETOOTH_AVAILABLE = True
except ImportError:
    BLUETOOTH_AVAILABLE = False

# Globals for config and state
BASE_URL = None
PASSWORD = None
PRINTERS = None
BLUETOOTH_RFCOMM = {}  # Maps MAC address -> /dev/rfcommX

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

def setup_bluetooth_printers():
    """Bind all Bluetooth printers to /dev/rfcommX once at startup"""
    global BLUETOOTH_RFCOMM
    index = 0
    for printer_id, cfg in PRINTERS.items():
        if cfg.get("type") != "bluetooth":
            continue
        mac_address = cfg.get("connection_data", {}).get("mac_address")
        if not mac_address:
            print(f"Bluetooth printer {printer_id} has no MAC address. Skipping.")
            continue
        rfcomm_device = f"/dev/rfcomm{index}"
        # Release if already bound
        if os.path.exists(rfcomm_device):
            subprocess.run(["sudo", "rfcomm", "release", rfcomm_device], check=False)
        # Bind
        try:
            subprocess.run(
                ["sudo", "rfcomm", "bind", rfcomm_device, mac_address, "1"],
                check=True
            )
            print(f"Bound {mac_address} to {rfcomm_device}")
            BLUETOOTH_RFCOMM[mac_address] = rfcomm_device
            index += 1
        except subprocess.CalledProcessError as e:
            print(f"Failed to bind {mac_address} to {rfcomm_device}: {e}")

def print_receipt(text, printer_cfg, printer_id=None):
    try:
        printer_type = printer_cfg.get("type")
        conn = printer_cfg.get("connection_data", {})

        if printer_type == "usb":
            vendor_id = int(conn["vendor_id"], 16)
            product_id = int(conn["product_id"], 16)
            p = Usb(vendor_id, product_id)

        elif printer_type == "network":
            host = conn.get("ip_address") or conn.get("host")
            port = int(conn.get("port", 9100))
            p = Network(host, port=port)

        elif printer_type == "bluetooth":
            mac_address = conn.get("mac_address")
            if not mac_address:
                print(f"Bluetooth printer {printer_id}: No MAC address provided.")
                return False
            rfcomm_device = BLUETOOTH_RFCOMM.get(mac_address)
            if not rfcomm_device:
                print(f"No RFCOMM device mapped for {mac_address}")
                return False
            p = Serial(devfile=rfcomm_device, baudrate=19200)

        else:
            print(f"Unknown printer type: {printer_type}")
            return False

        # Print
        p.text(text)
        p.set()  # Optional: Reset formatting
        p.cut()
        p.close()
        print(f"Printed on printer '{printer_id}' with config: {printer_cfg}")
        return True

    except Exception as e:
        print(f"Error printing on printer '{printer_id}': {e}")
        return False

def on_message(ch, method, properties, body):
    global PRINTERS
    try:
        message = json.loads(body)
        msg_type = message.get("type")
        action = message.get("action")

        # Reload printer config if printer update/create/delete
        if msg_type == "printer" and action in ("update", "create", "delete"):
            print(f"Printer config change detected: {action}. Reloading printers...")
            token = get_access_token(BASE_URL, PASSWORD)
            PRINTERS = fetch_printers(BASE_URL, token)
            setup_bluetooth_printers()  # Rebind Bluetooth printers
            print(f"Reloaded printers: {list(PRINTERS.keys())}")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        printer_id = message.get("printer_id")
        printer_cfg = PRINTERS.get(printer_id)
        if printer_cfg:
            receipt_text = "\n".join(message.get("lines", []))
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

def start_rabbitmq_consumer(RABBITMQ_URL, QUEUE_NAME):
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
    RABBITMQ_URL = config["rabbitmq_url"]
    QUEUE_NAME = config["queue_name"]

    token = get_access_token(BASE_URL, PASSWORD)
    PRINTERS = fetch_printers(BASE_URL, token)
    print(f"Available printers by name: {list(PRINTERS.keys())}")

    # Bind Bluetooth printers at startup
    setup_bluetooth_printers()

    start_rabbitmq_consumer(RABBITMQ_URL, QUEUE_NAME)

if __name__ == "__main__":
    main()
