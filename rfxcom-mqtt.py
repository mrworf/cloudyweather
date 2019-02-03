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
import threading
import argparse
import datetime
import traceback
import re
from array import array
from struct import unpack

import paho.mqtt.client as mqtt

class SensorMapping:
  def __init__(self):
    self.sensorMap = {}
    self.loadConfig()

  def getMapping(self, sensorId, sensorChannel):
    sensor = '%d.%d' % (sensorId, sensorChannel)
    if sensor in self.sensorMap:
      return self.sensorMap[sensor]
    return []

  def transposeData(self, topic, data):
    # First, find all fields
    keys = re.findall('\{([^\}]+)\}', topic)
    for key in keys:
      if key in data:
        value = data[key]
      else:
        print('Warning: Invalid key "%s"' % key)
        return None, None
      topic = topic.replace('{%s}' % key, str(value))
    # Finally, split the topic into key/value
    return topic.split(':')

  def loadConfig(self):
    current = None
    with open('sensors.conf', 'r') as file:
      lc = 0
      for oline in file:
        lc += 1
        line = oline.strip()
        if line == '' or line[0] == '#':
          continue
        if oline[0] == ' ' or oline[0] == '\t':
          topic = True
        else:
          topic = False
        if topic and current is None:
          print('Warning: Line %d, topic defined without sensor, ignored' % lc)
          continue
        if not topic:
          parts = line.split()
          if len(parts) != 4:
            print('Warning: Line %d, sensor definition line is incorrect' % lc)
            continue
          sensor = "%s.%s" % (parts[1], parts[3])
          current = sensor
          if current not in self.sensorMap:
            self.sensorMap[current] = []
        else:
          self.sensorMap[current].append(line)
#    print('Sensor configuration loaded:')
#    for sensor in self.sensorMap:
#      print('Sensor %s:' % sensor)
#      for item in self.sensorMap[sensor]:
#        print('  %s' % item)

class CloudySensor:
  def __init__(self):
    self.tid = threading.current_thread()
    self.blacklist = [
      32, # Security
      33, # Security,
      90, # Energy

    ]
    self.sensors = {}

  def processTempHumid(self, data):
    # Temp, Humidity, Flags, Battery & Signal
    (temp_hi, temp_lo, humidity, sigbat) = unpack(">BBBxB", data)

    temp = ((temp_hi & 0x7F) << 8 | temp_lo) / 10.0
    if temp_hi & 0x80:
      temp = -temp
    signal = (sigbat >> 4 & 0x0f)
    battery = (sigbat & 0x0f)

    return {
      "temperature.celsius" : temp,
      'temperature.farenheit' : round((temp * 1.8) + 32.0, 1),
      "humidity" : humidity,
      "signal" : signal,
      "battery" : battery
    }

  def processRain(self, data):
    # Temp, Humidity, Flags, Battery & Signal
    (rate_hi, rate_lo, total_hi, total_mi, total_lo, sigbat) = unpack(">BBBBBB", data)

    rate = (rate_hi << 8 | rate_lo) / 100.0
    total = (total_hi << 16 | total_mi << 8 | total_lo) / 10.0
    signal = (sigbat >> 4 & 0x0f)
    battery = (sigbat & 0x0f)

    return {
      "rain.rate" : rate,
      "rain.total" : total,
      "signal" : signal,
      "battery" : battery
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
      "uv" : uv,
      "signal" : signal,
      "battery" : battery
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
      "wind.direction" : wind,
      "wind.speed.current" : speed,
      "wind.speed.average" : avg,
      "signal" : signal,
      "battery" : battery
    }

  def getSensors(self, type=None):
    result = {}
    for s in self.sensors:
      if type is not None and self.sensors[s]['type'] != int(type):
        continue
      result[s] = {"name":self.sensors[s]['name'], "type":self.sensors[s]['type']}
    return result

  def getSensor(self, sensor):
    sensor = str(sensor)
    if sensor in self.sensors:
      return self.sensors[sensor]
    return {}

  def processEvent(self, data):
    stype = ord(data[0])
    subtype = ord(data[1])
    sensor = ord(data[3]) << 8 | ord(data[4])
    sensor_major = ord(data[3])
    sensor_minor = ord(data[4])
    index = str(sensor)
    data = data[5:]

    # Some sensors are never interesting
    if stype in self.blacklist:
      return None # Return none to avoid spurious error messages

    result = {
      'sensor.id' : sensor_major,
      'sensor.channel' : sensor_minor,
      'data' : None
    }

    try:
      if stype == 82:
        result['type'] = 'temperature'
        result['data'] = self.processTempHumid(data)
      elif stype == 86:
        result['type'] = 'wind'
        result['data'] = self.processWind(subtype, data)
      elif stype == 87:
        result['type'] = 'uv'
        result['data'] = self.processUV(subtype, data)
      elif stype == 85:
        result['type'] = 'rain'
        result['data'] = self.processRain(data)
      else:
        print "Warning: Type %d is unsupported" % stype
        return None
    except:
      print "Decoder failed on: %d:%s" % (stype, data.encode('hex'))
      traceback.print_exc()
      return None

    # Don't bother with the remaining steps if data didn't change
    if index in self.sensors and repr(self.sensors[index]) == repr(result):
      return None

    self.sensors[index] = result
    return result

class rfxcomMonitor(threading.Thread):
  def __init__(self, port, detect=False):
    threading.Thread.__init__(self)
    self.daemon = True
    self.port = port
    self.detect = detect
    self.mqtt = None

  def start(self, mqtt):
    self.mqtt = mqtt
    threading.Thread.start(self)

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
    self.cloudy = CloudySensor()
    self.mapping = SensorMapping()

    # Keep track of last message so we don't flood the server (topic : value)
    lastActivity = {}

    started = time.time()
    if self.detect:
      print('Running detection for 60s, please be patient...')

    while True:
      while True:
        if self.detect and time.time() > (started + 60):
          break
        size = ser.read(1)
        if len(size) == 1:
          break
      if self.detect and time.time() > (started + 60):
        break

      size = int(size.encode('hex'), 16)
      if size == 0:
        continue

      data = ser.read(size)
      if len(data) != size:
        print "Fail, got %d bytes, expected %d!" % (len(data), size)
      else:
        result = self.cloudy.processEvent(data)
        if result is not None and not self.detect:
          topics = self.mapping.getMapping(result['sensor.id'], result['sensor.channel'])
          for topic in topics:
            topic, value = self.mapping.transposeData(topic, result['data'])
            if topic is None:
              continue
            if topic in lastActivity and lastActivity[topic] == value:
              continue
            lastActivity[topic] = value
            print('Publish %s to %s' % (value, topic))
            client.publish(topic, value)

    print "All detected sensors (if prefixed with asterisk, it's already in your sensors.conf):"
    for key in self.cloudy.sensors:
      sensor = self.cloudy.sensors[key]
      if len(self.mapping.getMapping(sensor['sensor.id'], sensor['sensor.channel'])) == 0:
        mapped = ' '
      else:
        mapped = '*'
      print('%s Channel %2d, Id %4d, Type %s (%s)' % (mapped, sensor['sensor.channel'], sensor['sensor.id'], sensor['type'], repr(sensor['data'])))

parser = argparse.ArgumentParser(description="Cloudy Weather - An RFXCOM based weather station", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument('--logfile', metavar="FILE", help="Log to file instead of stdout")
parser.add_argument('--serial', metavar="serial", default="/dev/ttyUSB0", help="Which serialport to read sensor data from")
parser.add_argument('--detect', action='store_true', help='Run for 60s and show all detected sensors, will not report to MQTT broker')
parser.add_argument('mqtt', help='MQTT Broker to publish topics')

cmdline = parser.parse_args()
rfxcom = rfxcomMonitor(cmdline.serial, detect=cmdline.detect)

if cmdline.detect:
  rfxcom.run()
else:
  client = mqtt.Client()
  #client.on_connect = on_connect
  #client.on_message = on_message
  client.connect(cmdline.mqtt, 1883, 60)
  rfxcom.start(client)
  client.loop_forever()
