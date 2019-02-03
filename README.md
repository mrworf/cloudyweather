# RFXCOM MQTT Bridge

This service uses the rfxcom USB device to gather input from all kinds of
Oregon Scientific sensors (wind, rain, temperature, etc.) and then publishes
it using the mqtt protocol.

# Installing

The service has the following dependencies:

- pip
- pyserial
- paho-mqtt

And of course, it needs a RFXCOM interface attached via USB

The following instructions assumes you're running a ubuntu/debian flavor:

```
sudo apt-get install python-pip python-dev python-paho-mqtt
sudo pip install pyserial
```

# Running it

First, configure the `sensors.conf` file to accurately reflect your setup. 
To find out what sensors the rfxcom device can see, use the `--detect` option

Next run the tool and it will publish any and all data via MQTT topics to your
selected broker

# What happened the old software?

It was poorly maintained and worked so-so. Learning about MQTT and what it is used
for made it very to see that I should change direction.


