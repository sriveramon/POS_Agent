from escpos import BluetoothConnection
from escpos.impl.epson import GenericESCPOS

conn = BluetoothConnection.create('66:32:30:73:CA:C8')
printer = GenericESCPOS(conn)
printer.init()
printer.text('Hello World!')