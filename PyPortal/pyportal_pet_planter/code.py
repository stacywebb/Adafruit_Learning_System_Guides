# SPDX-FileCopyrightText: 2020 Brent Rubell for Adafruit Industries
#
# SPDX-License-Identifier: MIT

from os import getenv
import time

import board
import busio
from digitalio import DigitalInOut
import adafruit_connection_manager
from adafruit_esp32spi import adafruit_esp32spi, adafruit_esp32spi_wifimanager
import adafruit_imageload
import displayio
import neopixel
from adafruit_bitmap_font import bitmap_font
from adafruit_display_text.label import Label
from adafruit_io.adafruit_io import IO_MQTT
import adafruit_minimqtt.adafruit_minimqtt as MQTT
from adafruit_pyportal import PyPortal
from adafruit_seesaw.seesaw import Seesaw
from simpleio import map_range

# Get WiFi details and Adafruit IO keys, ensure these are setup in settings.toml
# (visit io.adafruit.com if you need to create an account, or if you need your Adafruit IO key.)
ssid = getenv("CIRCUITPY_WIFI_SSID")
password = getenv("CIRCUITPY_WIFI_PASSWORD")
aio_username = getenv("ADAFRUIT_AIO_USERNAME")
aio_key = getenv("ADAFRUIT_AIO_KEY")

if None in [ssid, password, aio_username, aio_key]:
    raise RuntimeError(
        "WiFi and Adafruit IO settings are kept in settings.toml, "
        "please add them there. The settings file must contain "
        "'CIRCUITPY_WIFI_SSID', 'CIRCUITPY_WIFI_PASSWORD', "
        "'ADAFRUIT_AIO_USERNAME' and 'ADAFRUIT_AIO_KEY' at a minimum."
    )

#---| User Config |---------------

# How often to poll the soil sensor, in seconds
# Polling every 30 seconds or more may cause connection timeouts
DELAY_SENSOR = 15

# How often to send data to adafruit.io, in minutes
DELAY_PUBLISH = 5

# Maximum soil moisture measurement
SOIL_LEVEL_MAX = 500.0

# Minimum soil moisture measurement
SOIL_LEVEL_MIN= 350.0

#---| End User Config |---------------

# Background image
BACKGROUND = "/images/roots.bmp"
# Icons for water level and temperature
ICON_LEVEL = "/images/icon-wetness.bmp"
ICON_TEMP = "/images/icon-temp.bmp"
WATER_COLOR = 0x16549E

# Audio files
wav_water_high = "/sounds/water-high.wav"
wav_water_low = "/sounds/water-low.wav"

# the current working directory (where this file is)
cwd = ("/"+__file__).rsplit('/', 1)[0]

# Set up i2c bus
i2c_bus = busio.I2C(board.SCL, board.SDA)

# Initialize soil sensor (s.s)
ss = Seesaw(i2c_bus, addr=0x36)

# PyPortal ESP32 AirLift Pins
esp32_cs = DigitalInOut(board.ESP_CS)
esp32_ready = DigitalInOut(board.ESP_BUSY)
esp32_reset = DigitalInOut(board.ESP_RESET)

spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
esp = adafruit_esp32spi.ESP_SPIcontrol(spi, esp32_cs, esp32_ready, esp32_reset)
status_pixel = neopixel.NeoPixel(board.NEOPIXEL, 1, brightness=0.2)
wifi = adafruit_esp32spi_wifimanager.WiFiManager(esp, ssid, password, status_pixel=status_pixel)

# Initialize PyPortal Display
display = board.DISPLAY

WIDTH = board.DISPLAY.width
HEIGHT = board.DISPLAY.height

# Initialize new PyPortal object
pyportal = PyPortal(esp=esp,
                    external_spi=spi)

# Set backlight level
pyportal.set_backlight(0.5)

# Create a new DisplayIO group
splash = displayio.Group()

# show splash group
display.root_group = splash

# Palette for water bitmap
palette = displayio.Palette(2)
palette[0] = 0x000000
palette[1] = WATER_COLOR
palette.make_transparent(0)

# Create water bitmap
water_bmp = displayio.Bitmap(display.width, display.height, len(palette))
water = displayio.TileGrid(water_bmp, pixel_shader=palette)
splash.append(water)

print("drawing background..")
# Load background image
try:
    bg_bitmap, bg_palette = adafruit_imageload.load(BACKGROUND,
                                                    bitmap=displayio.Bitmap,
                                                    palette=displayio.Palette)
# Or just use solid color
except (OSError, TypeError):
    BACKGROUND = BACKGROUND if isinstance(BACKGROUND, int) else 0x000000
    bg_bitmap = displayio.Bitmap(display.width, display.height, 1)
    bg_palette = displayio.Palette(1)
    bg_palette[0] = BACKGROUND
bg_palette.make_transparent(0)
background = displayio.TileGrid(bg_bitmap, pixel_shader=bg_palette)

# Add background to display
splash.append(background)

print('loading fonts...')
# Fonts within /fonts/ folder
font = cwd+"/fonts/GothamBlack-50.bdf"
font_small = cwd+"/fonts/GothamBlack-25.bdf"

# pylint: disable=syntax-error
data_glyphs = b'0123456789FC-* '
font = bitmap_font.load_font(font)
font.load_glyphs(data_glyphs)

font_small = bitmap_font.load_font(font_small)
full_glyphs = b'0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-,.: '
font_small.load_glyphs(full_glyphs)

# Label to display Adafruit IO status
label_status = Label(font_small)
label_status.x = 305
label_status.y = 10
splash.append(label_status)

# Create a label to display the temperature
label_temp = Label(font)
label_temp.x = 35
label_temp.y = 300
splash.append(label_temp)

# Create a label to display the water level
label_level = Label(font)
label_level.x = display.width - 130
label_level.y = 300
splash.append(label_level)

print('loading icons...')
# Load temperature icon
icon_tmp_bitmap, icon_palette = adafruit_imageload.load(ICON_TEMP,
                                                        bitmap=displayio.Bitmap,
                                                        palette=displayio.Palette)
icon_palette.make_transparent(0)
icon_tmp_bitmap = displayio.TileGrid(icon_tmp_bitmap,
                                     pixel_shader=icon_palette,
                                     x=0, y=280)
splash.append(icon_tmp_bitmap)

# Load level icon
icon_lvl_bitmap, icon_palette = adafruit_imageload.load(ICON_LEVEL,
                                                        bitmap=displayio.Bitmap,
                                                        palette=displayio.Palette)
icon_palette.make_transparent(0)
icon_lvl_bitmap = displayio.TileGrid(icon_lvl_bitmap,
                                     pixel_shader=icon_palette,
                                     x=315, y=280)
splash.append(icon_lvl_bitmap)

# Connect to WiFi
label_status.text = "Connecting..."
while not esp.is_connected:
    try:
        wifi.connect()
    except (RuntimeError, ConnectionError) as e:
        print("could not connect to AP, retrying: ",e)
        wifi.reset()
        continue
print("Connected to WiFi!")

pool = adafruit_connection_manager.get_radio_socketpool(esp)
ssl_context = adafruit_connection_manager.get_radio_ssl_context(esp)

# Initialize a new MQTT Client object
mqtt_client = MQTT.MQTT(broker="io.adafruit.com",
                        username=aio_username,
                        password=aio_key,
                        socket_pool=pool,
                        ssl_context=ssl_context)

# Adafruit IO Callback Methods
# pylint: disable=unused-argument
def connected(client):
    # Connected function will be called when the client is connected to Adafruit IO.
    print('Connected to Adafruit IO!')

def subscribe(client, userdata, topic, granted_qos):
    # This method is called when the client subscribes to a new feed.
    print('Subscribed to {0} with QOS level {1}'.format(topic, granted_qos))

# pylint: disable=unused-argument
def disconnected(client):
    # Disconnected function will be called if the client disconnects
    # from the Adafruit IO MQTT broker.
    print("Disconnected from Adafruit IO!")

# Initialize an Adafruit IO MQTT Client
io = IO_MQTT(mqtt_client)

# Connect the callback methods defined above to the Adafruit IO MQTT Client
io.on_connect = connected
io.on_subscribe = subscribe
io.on_disconnect = disconnected

# Connect to Adafruit IO
print("Connecting to Adafruit IO...")
io.connect()
label_status.text = " "
print("Connected!")

fill_val = 0.0
def fill_water(fill_percent):
    """Fills the background water.
    :param float fill_percent: Percentage of the display to fill.

    """
    assert fill_percent <= 1.0, "Water fill value may not be > 100%"
    # pylint: disable=global-statement
    global fill_val

    if fill_val > fill_percent:
        for _y in range(int((board.DISPLAY.height-1) - ((board.DISPLAY.height-1)*fill_val)),
                        int((board.DISPLAY.height-1) - ((board.DISPLAY.height-1)*fill_percent))):
            for _x in range(1, board.DISPLAY.width-1):
                water_bmp[_x, _y] = 0
    else:
        for _y in range(board.DISPLAY.height-1,
                        (board.DISPLAY.height-1) - ((board.DISPLAY.height-1)*fill_percent), -1):
            for _x in range(1, board.DISPLAY.width-1):
                water_bmp[_x, _y] = 1
    fill_val = fill_percent

def display_temperature(temp_val, is_celsius=False):
    """Displays the temperature from the STEMMA soil sensor
    on the PyPortal Titano.
    :param float temp: Temperature value.
    :param bool is_celsius:

    """
    if not is_celsius:
        temp_val = (temp_val * 9 / 5) + 32 - 15
        print('Temperature: %0.0fF'%temp_val)
        label_temp.text = '%0.0fF'%temp_val
        return int(temp_val)
    else:
        print('Temperature: %0.0fC'%temp_val)
        label_temp.text = '%0.0fC'%temp_val
        return int(temp_val)

# initial reference time
initial = time.monotonic()
while True:
    # Explicitly pump the message loop
    # to keep the connection active
    try:
        io.loop()
    except (ValueError, RuntimeError, ConnectionError, OSError) as e:
        print("Failed to get data, retrying...\n", e)
        wifi.reset()
        continue
    now = time.monotonic()

    print("reading soil sensor...")
    # Read capactive
    moisture = ss.moisture_read()
    label_level.text = str(moisture)

    # Convert into percentage for filling the screen
    moisture_percentage = map_range(float(moisture), SOIL_LEVEL_MIN, SOIL_LEVEL_MAX, 0.0, 1.0)

    # Read temperature
    temp = ss.get_temp()
    temp = display_temperature(temp)

    # fill display
    print("filling disp..")
    fill_water(moisture_percentage)
    print("disp filled..")

    print("temp: " + str(temp) + "  moisture: " + str(moisture))

    # Play water level alarms
    if moisture <= SOIL_LEVEL_MIN:
        print("Playing low water level warning...")
        pyportal.play_file(wav_water_low)
    elif moisture >= SOIL_LEVEL_MAX:
        print("Playing high water level warning...")
        pyportal.play_file(wav_water_high)


    if now - initial > (DELAY_PUBLISH * 60):
        try:
            print("Publishing data to Adafruit IO...")
            label_status.text = "Sending to IO..."
            io.publish("moisture", moisture)
            io.publish("temperature", temp)
            print("Published")
            label_status.text = "Data Sent!"

            # reset timer
            initial = now
        except (ValueError, RuntimeError, ConnectionError, OSError) as e:
            label_status.text = "ERROR!"
            print("Failed to get data, retrying...\n", e)
            wifi.reset()
    time.sleep(DELAY_SENSOR)
