FROM python:3.7-alpine

ENV SERIAL=/dev/ttyUSB0
ENV MQTT=
ENV CONFIG=sensors.conf
END DETECT=

WORKDIR /usr/src/app
COPY . ./

RUN pip3 install pyserial paho-mqtt

CMD /usr/src/app/rfxcom-mqtt.py $DETECT --serial "$SERIAL" --mqtt "$MQTT" --config "$CONFIG"
