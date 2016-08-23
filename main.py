#!/usr/bin/env python
#
# For help with decoding messages from RFXCOM, please see the OpenHAB project,
# specifically:
#
# org.openhab.binding.rfxcom/src/main/java/org/openhab/binding/rfxcom/internal/messages
#
# The RFXComBaseMessage.java points out all the types they support and is what I've based
# my implementation on as well.
#
#
import sys
import time
import serial
import sqlite3
import threading
import argparse
import datetime
import traceback
from array import array
from struct import unpack

from flask import Flask, jsonify, abort, request
from flask_cors import CORS, cross_origin

class Oregon:
  def __init__(self, dbfile):
    self.dbfile = dbfile
    self.db = sqlite3.connect(self.dbfile)
    self.tid = threading.current_thread()
    self.blacklist = [
      32, # Security
      33, # Security,
      90, # Energy

    ]

    sql = '''SELECT * FROM SENSORS'''
    result = self.db.execute(sql)
    self.sensors = {}
    for entry in result:
      self.sensors[str(entry[0])] = {"type":entry[1], "name":entry[2]}

  def processTempHumid(self, data):
    # Temp, Humidity, Flags, Battery & Signal
    (temp_hi, temp_lo, humidity, sigbat) = unpack(">BBBxB", data)

    temp = ((temp_hi & 0x7F) << 8 | temp_lo) / 10.0
    if temp_hi & 0x80:
      temp = -temp
    signal = (sigbat >> 4 & 0x0f)
    battery = (sigbat & 0x0f)

    return {
      "log" : "%.1fC, %d%% (Signal %d, Battery %d)" % (temp, humidity, signal, battery),
      "table" : "TH_DATA",
      "fields" : "TEMPERATURE,HUMIDITY,SIGNAL,BATTERY",
      "values" : "%f,%d,%d,%d" % (temp, humidity, signal, battery),
      "data" : { "temperature" : temp, "humidity" : humidity, "signal" : signal, "battery" : battery}
    }

  def processRain(self, data):
    # Temp, Humidity, Flags, Battery & Signal
    (rate_hi, rate_lo, total_hi, total_mi, total_lo, sigbat) = unpack(">BBBBBB", data)

    rate = (rate_hi << 8 | rate_lo) / 100.0
    total = (total_hi << 16 | total_mi << 8 | total_lo) / 10.0
    signal = (sigbat >> 4 & 0x0f)
    battery = (sigbat & 0x0f)

    return {
      "log" : "%.2f mm, %.1f mm total (Signal %d, Battery %d)" % (rate, total, signal, battery),
      "table" : "RAIN_DATA",
      "fields" : "RATE,TOTAL,SIGNAL,BATTERY",
      "values" : "%f,%d,%d,%d" % (rate, total, signal, battery),
      "data" : { "rate" : rate, "total" : total, "signal" : signal, "battery" : battery}
    }

  def processUV(self, subtype, data):
    # UV, Flags, Battery & Signal
    (temp_hi, temp_lo, uv, sigbat) = unpack(">BBBB", data)

    temp = ((temp_hi & 0x7F) << 8 | temp_lo) / 10.0
    if temp_hi & 0x80:
      temp = -temp
    signal = (sigbat >> 4 & 0x0f)
    battery = (sigbat & 0x0f)

    if subtype == 3:
      extras = ", %.1fC" % temp
    else:
      extras = ""

    return {
      "log" : "%d UV%s (Signal %d, Battery %d)" % (uv, extras, signal, battery),
      "table" : "UV_DATA",
      "fields" : "UV,TEMPERATURE,VALIDTEMP,SIGNAL,BATTERY",
      "values" : "%d,%f,%d,%d,%d" % (uv, temp, subtype == 3, signal, battery),
      "data" : { "temperature" : temp, "uv" : uv, "signal" : signal, "battery" : battery, "valid_temperature" : subtype == 3}
    }

  def processWind(self, subtype, data):
    # direction, avg speed, speed

    if len(data) != 11:
      print "ERROR: Do not support this kind of wind sensor"
      return None

    (wind_hi, wind_lo, avg_hi, avg_lo, speed_hi, speed_lo, temp_hi, temp_lo, chill_hi, chill_lo, sigbat) = unpack(">BBBBBBBBBBB", data)

    wind = wind_hi << 8 | wind_lo
    avg = (avg_hi << 8 | avg_lo) / 10
    speed = (speed_hi << 8 | speed_lo) / 10
    temp = ((temp_hi & 0x7F) << 8 | temp_lo) / 10.0
    if temp_hi & 0x80:
      temp = -temp
    chill = ((chill_hi & 0x7F) << 8 | chill_lo) / 10.0
    if chill_hi & 0x80:
      chill = -chill

    signal = (sigbat >> 4 & 0x0f)
    battery = (sigbat & 0x0f)

    if subtype == 4:
      extras = " %.1fC, %.1fCF" % (temp, chill)
    else:
      extras = ""

    return {
      "log" : "%d direction, %.1f m/s (%.1f avg)%s (Signal %d, Battery %d)" % (wind, speed, avg, extras, signal, battery),
      "table" : "WIND_DATA",
      "fields" : "DIRECTION,AVERAGE,INSTANT,TEMPERATURE,VALIDTEMP,CHILLFACTOR,VALIDCHILL,SIGNAL,BATTERY",
      "values" : "%d,%f,%f,%f,%d,%f,%d,%d,%d" % (wind, avg, speed, temp, subtype == 4, chill, subtype == 4, signal, battery),
      "data" : { "wind" : wind, "average" : avg, "speed" : speed, "signal" : signal, "battery" : battery, "temperature":temp, "chillfactor":chill, "valid_temperature": subtype == 3, "valid_chillfactor" : subtype == 3}
    }

  def getSensors(self, type=None):
    result = {}
    for s in self.sensors:
      if type is not None and self.sensors[s]['type'] != int(type):
        continue
      result[s] = {"name":self.sensors[s]['name'], "type":self.sensors[s]['type']}
    return result

  def setSensorName(self, sensor, name):
    if name == "":
      return

    # Make DB access thread safe
    if self.tid != threading.current_thread():
      db = sqlite3.connect(self.dbfile)
    else:
      db = self.db

    sql = '''UPDATE SENSORS SET NAME = "%s" WHERE ID = %d''' % (name, sensor)
    try:
      db.execute(sql)
      db.commit()

      # Update memory copy
      self.sensors[str(sensor)]['name'] = name
    except sqlite3.OperationalError as e:
      print e

    if self.tid != threading.current_thread():
      db.close()

  def getSensor(self, sensor):
    sensor = str(sensor)
    if sensor in self.sensors:
      return self.sensors[sensor]
    return {}

  def processEvent(self, data):
    stype = ord(data[0])
    subtype = ord(data[1])
    sensor = ord(data[3]) << 8 | ord(data[4])
    index = str(sensor)
    name = "Sensor 0x%02x.%d" % (ord(data[3]), ord(data[4]))
    data = data[5:]

    # Some sensors are never interesting
    if stype in self.blacklist:
      return True # Return true to avoid spurious error messages

    # Store sensor (if new)
    if index not in self.sensors:
      statement = 'INSERT OR IGNORE INTO SENSORS (ID,TYPE,NAME) VALUES (%d,%d,"%s")' % (sensor, stype, name)

      # Store this in the database for prosperity
      try:
        self.db.execute(statement)
        self.db.commit()
      except sqlite3.OperationalError as e:
        print e

    result = None
    try:
      if stype == 82:
        result = self.processTempHumid(data)
      elif stype == 86:
        result = self.processWind(subtype, data)
      elif stype == 87:
        result = self.processUV(subtype, data)
      elif stype == 85:
        result = self.processRain(data)
      else:
        print "Warning: Type %d is unsupported" % stype
        return True
    except:
      print "Decoder failed on: %d:%s" % (stype, data.encode('hex'))
      traceback.print_exc()
      return True

    # Set the type
    result['data']['type'] = stype

    # Also keep the name (or add if new sensor)
    if index in self.sensors:
      result['data']['name'] = self.sensors[index]['name']
    else:
      result['data']['name'] = name

    print "(%3d:%5d) %s" % (stype, sensor, result["log"])

    # Don't bother with the remaining steps if data didn't change
    if index in self.sensors and repr(self.sensors[index]) == repr(data):
      return True

    self.sensors[index] = result['data']

    try:
      statement = 'INSERT INTO %s (TS,SENSOR,%s) VALUES (%d,%d,%s)' % (result["table"], result["fields"], int(time.time()), sensor, result["values"])
      self.db.execute(statement)
      self.db.commit()
    except sqlite3.OperationalError as e:
      print e
    return True

class rfxcomMonitor(threading.Thread):
  def __init__(self, port, dbfile):
    threading.Thread.__init__(self)
    self.daemon = True
    self.port = port
    self.dbfile = dbfile

  def getOregon(self):
    return self.oregon

  def run(self):
    # configure the serial connections (the parameters differs on the device you are connecting to)
    ser = serial.Serial(
      port=self.port,
      baudrate=38400,
      parity=serial.PARITY_NONE,
      stopbits=serial.STOPBITS_ONE,
      bytesize=serial.EIGHTBITS,
      timeout=1,
    )

    ser.isOpen()
    self.oregon = Oregon(self.dbfile)

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
        if not self.oregon.processEvent(data):
          print "Unhandled event: " + data.encode('hex')

parser = argparse.ArgumentParser(description="Oregon Scientific via RFXCOM - Keeping track of the weather", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument('--logfile', metavar="FILE", help="Log to file instead of stdout")
parser.add_argument('--database', metavar="DATABASE", default="oregon.db", help="Where to store data")
parser.add_argument('--serial', metavar="serial", default="/dev/ttyUSB0", help="Which serialport to read sensor data from")
parser.add_argument('--port', default=8070, type=int, help="Port to listen on")
parser.add_argument('--listen', metavar="ADDRESS", default="0.0.0.0", help="Address to listen on")
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
    VALIDTEMP   INT   NOT NULL,
    SIGNAL      INT   NOT NULL,
    BATTERY     INT   NOT NULL);''')

db.execute('''CREATE TABLE IF NOT EXISTS WIND_DATA
   (TS          INT   NOT NULL,
    SENSOR      INT   NOT NULL,
    DIRECTION   INT   NOT NULL,
    AVERAGE     REAL  NOT NULL,
    INSTANT     REAL  NOT NULL,
    TEMPERATURE REAL  NOT NULL,
    CHILLFACTOR REAL  NOT NULL,
    VALIDTEMP   INT   NOT NULL,
    VALIDCHILL  INT   NOT NULL,
    SIGNAL      INT   NOT NULL,
    BATTERY     INT   NOT NULL);''')

db.execute('''CREATE TABLE IF NOT EXISTS RAIN_DATA
   (TS          INT   NOT NULL,
    SENSOR      INT   NOT NULL,
    RATE        REAL  NOT NULL,
    TOTAL       REAL  NOT NULL,
    SIGNAL      INT   NOT NULL,
    BATTERY     INT   NOT NULL);''')

rfxcom = rfxcomMonitor(cmdline.serial, cmdline.database)
rfxcom.start()

# Create the REST interface
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

@app.route('/sensors', defaults={"type": None})
@app.route('/sensors/<type>')
def api_sensors(type):
  msg = rfxcom.getOregon().getSensors(type)
  json = jsonify(msg)
  json.status_code = 200
  return json

@app.route('/sensor/<id>')
def api_sensor(id):
  msg = rfxcom.getOregon().getSensor(id)
  json = jsonify(msg)
  json.status_code = 200
  return json

@app.route('/sensor/update', methods=['POST'])
def api_sensorUpdate():
  id = request.form.get('id', type=int)
  name = request.form.get('name', type=str)
  if id is None:
    print "Id is none"
    return
  if name is None:
    print "Name is none"
    return

  rfxcom.getOregon().setSensorName(id, name)
  msg = rfxcom.getOregon().getSensor(id)
  json = jsonify(msg)
  json.status_code = 200
  return json

if __name__ == "__main__":
  app.debug = False
  app.run(host=cmdline.listen, port=cmdline.port)
