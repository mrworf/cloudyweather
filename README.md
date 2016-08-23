Cloudy Weather
==============

An RFXCOM based weather station which is very much a work-in-progress
(formerly called rfxcom). It will use an rfxcom USB receiver to register
all found temperature, humidity, rain and wind sensors to a local database.

All stored data is available through a REST interface, but current
implementation only exposes latest values.

Installing
==========

The service has the following dependencies:

- pip
- flask
- pyserial

And of course, it needs a RFXCOM interface attached via USB

The following instructions assumes you're running a ubuntu/debian flavor:

```
sudo apt-get install python-pip
sudo apt-get install python-dev
sudo pip install flask
sudo pip install flask-cors
sudo pip install pyserial
```

Running it
==========

In almost all cases, simply running the main.py from commandline will be
sufficient, but if you need to, you can specify both serial port 
(default /dev/ttyUSB0) and database (default cloudy.db)

Accessing it
============

It serves up a REST interface on port 8070 where you can get the following
data:

```
/sensors/
```

Returns a list of all sensors

```
/sensors/<type>/
```

Returns a list of sensors which are of <type>

```
/sensor/<id>
```

Returns the latest status of the sensor <id>

