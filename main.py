#!/usr/bin/env python
import sys
import time
import serial
import sqlite3
import threading
import argparse
import datetime
from array import array
from struct import unpack

class Oregon:
  def __init__(self):
    self.lastEntry = {}

  def processTempHumid(self, data):
    # Temp, Humidity, Flags, Battery & Signal
    (temp_hi, temp_lo, humidity, sigbat) = unpack(">BBBxB", data)

    temp = (temp_hi << 8 | temp_lo) / 10.0
    signal = (sigbat >> 4 & 0x0f)
    battery = (sigbat & 0x0f)

    return {
      "log" : "%.1fC, %d%% (Signal %d, Battery %d)" % (temp, humidity, signal, battery),
      "table" : "TH_DATA",
      "fields" : "TEMPERATURE,HUMIDITY,SIGNAL,BATTERY",
      "values" : "%f,%d,%d,%d" % (temp, humidity, signal, battery)
    }

  def processUV(self, data):
    # UV, Flags, Battery & Signal
    (temp_hi, temp_lo, uv, sigbat) = unpack(">BBBB", data)

    temp = (temp_hi << 8 | temp_lo) / 10.0
    signal = (sigbat >> 4 & 0x0f)
    battery = (sigbat & 0x0f)

    return {
      "log" : "%d UV, %.1fC (Signal %d, Battery %d)" % (uv, temp, signal, battery),
      "table" : "UV_DATA",
      "fields" : "UV,TEMPERATURE,SIGNAL,BATTERY",
      "values" : "%d,%f,%d,%d" % (uv, temp, signal, battery)
    }

  def processWind(self, data):
    # direction, avg speed, speed
    (wind_hi, wind_lo, avg_hi, avg_lo, speed_hi, speed_lo, sigbat) = unpack(">BBBBBB", data)

    wind = wind_hi << 8 | wind_lo
    avg = avg_hi << 8 | avg_lo
    speed = speed_hi << 8 | speed_lo

    signal = (sigbat >> 4 & 0x0f)
    battery = (sigbat & 0x0f)

    return {
      "log" : "%d direction, %d m/s (%d avg) (Signal %d, Battery %d)" % (wind, speed, avg, signal, battery),
      "table" : "WIND_DATA",
      "fields" : "DIRECTION,AVERAGE,INSTANT,SIGNAL,BATTERY",
      "values" : "%d,%d,%d" % (wind, avg, speed, signal, battery)
    }

  def processEvent(self, db, data):
    stype = ord(data[0])
    subtype = ord(data[1])
    sensor = ord(data[3]) << 8 | ord(data[4])
    name = "Sensor 0x%02x.%d" % (ord(data[3]), ord(data[4]))
    data = data[5:]

    # Store sensor (if new)
    statement = 'INSERT OR IGNORE INTO SENSORS (ID,TYPE,NAME) VALUES (%d,%d,"%s")' % (sensor, stype, name)

    # Store this in the database for prosperity
    try:
      db.execute(statement)
      db.commit()
    except sqlite3.OperationalError as e:
      print e

    result = None
    try:
      if stype == 82:
        result = self.processTempHumid(data)
      elif stype == 86:
        result = self.processWind(data)
      elif stype == 87:
        result = self.processUV(data)
    except:
      print "Decoder failed on: " + data.encode('hex')
      return True

    if result is None:
      return False

    print "%s: %s" % (name, result["log"])
    content = repr(result)

    if sensor in self.lastEntry and self.lastEntry[sensor] == content:
      return True

    self.lastEntry[sensor] = content

    try:
      statement = 'INSERT INTO %s (TS,SENSOR,%s) VALUES (%d,%d,%s)' % (result["table"], result["fields"], int(time.time()), sensor, result["values"])
      db.execute(statement)
      db.commit()
    except sqlite3.OperationalError as e:
      print e
    return True

parser = argparse.ArgumentParser(description="Oregon Scientific via RFXCOM - Keeping track of the weather", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument('--logfile', metavar="FILE", help="Log to file instead of stdout")
parser.add_argument('--database', metavar="DATABASE", default="oregon.db", help="Where to store data")
parser.add_argument('--port', metavar="port", default="/dev/ttyUSB0", help="Which serialport to read sensor data from")
cmdline = parser.parse_args()

db = sqlite3.connect(cmdline.database)

# Setup the DB
db.execute('''CREATE TABLE IF NOT EXISTS SENSORS
   (ID   INT PRIMARY KEY  NOT NULL,
    TYPE INT              NOT NULL,
    NAME TEXT             NOT NULL);''')

db.execute('''CREATE TABLE IF NOT EXISTS TH_DATA
   (TS          INT   NOT NULL,
    SENSOR      INT   NOT NULL,
    TEMPERATURE REAL  NOT NULL,
    HUMIDITY    INT   NOT NULL,
    SIGNAL      INT   NOT NULL,
    BATTERY     INT   NOT NULL);''')

db.execute('''CREATE TABLE IF NOT EXISTS UV_DATA
   (TS          INT   NOT NULL,
    SENSOR      INT   NOT NULL,
    UV          INT   NOT NULL,
    TEMPERATURE REAL  NOT NULL,
    SIGNAL      INT   NOT NULL,
    BATTERY     INT   NOT NULL);''')

# configure the serial connections (the parameters differs on the device you are connecting to)
ser = serial.Serial(
  port='/dev/ttyUSB0',
  baudrate=38400,
  parity=serial.PARITY_NONE,
  stopbits=serial.STOPBITS_ONE,
  bytesize=serial.EIGHTBITS,
  timeout=1,
)

oregon = Oregon()

ser.isOpen()

# Try reading a packet
while True:
  while True:
    size = ser.read(1)
    if len(size) == 1:
      break

  size = int(size.encode('hex'), 16)
  if size == 0:
    continue

  #print ("%02d bytes:" % size),
  data = ser.read(size)
  if len(data) != size:
    print "Fail, got %d bytes, expected %d!" % (len(data), size)
  else:
    if not oregon.processEvent(db, data):
      print "Unhandled event: " + data.encode('hex') 