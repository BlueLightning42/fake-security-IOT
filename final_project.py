import hashlib, os, binascii, time
import RPi.GPIO as GPIO 
from pad4pi import rpi_gpio
from binascii import hexlify
from threading import Timer

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from luma.led_matrix.device import max7219
from luma.core.interface.serial import spi, noop
from luma.core.render import canvas
from luma.core.virtual import viewport
from luma.core.legacy import text, show_message
from luma.core.legacy.font import proportional, CP437_FONT, TINY_FONT, SINCLAIR_FONT, LCD_FONT

# shoving alot of the google sheets api into this class to organize it.
class GoogleSheets:
    def __init__(self, username, scope, jsoncreds, clientname):
        self.username = username
        creds = ServiceAccountCredentials.from_json_keyfile_name(jsoncreds, scope)
        client = gspread.authorize(creds)
        self.sheet = client.open(clientname).sheet1
        self.session_password = self.get_creds()

    def get_row(self):
        '''Get row that contains the username'''
        usernames = list(filter(None, self.sheet.col_values(1)))
        if self.username in usernames:
            return str(usernames.index(self.username)+1) # return existing 
        else:
            return str(len(usernames)+1) # append new
    
    def get_creds(self):
        row = self.get_row()
        return self.sheet.cell(row, 2).value

    def store_password(self, password):
        '''Hash password and then update it on google cloud'''
        row = self.get_row()
        self.sheet.update_cell(row, 1, self.username)
        self.session_password = self.hash_password(password)
        self.sheet.update_cell(row, 2, self.session_password)

    # Not specifically related to googleSheets but organized here
    def hash_password(self, password):
        '''Hash a password for storing.'''
        salt = hashlib.sha256(os.urandom(60)).hexdigest().encode('ascii')
        pwdhash = hashlib.pbkdf2_hmac('sha512', password.encode('utf-8'), salt, 100000)
        pwdhash = hexlify(pwdhash)
        return (salt + pwdhash).decode('ascii')
      
    def verify_password(self, provided_password):
        '''Verify a stored password against one provided by user'''
        salt = self.session_password[:64].encode('ascii')
        stored_password = self.session_password[64:]
        pwdhash = hashlib.pbkdf2_hmac('sha512', provided_password.encode('utf-8'), salt, 100000)
        pwdhash = hexlify(pwdhash).decode('ascii')
        return pwdhash == stored_password

def track_keys(key):
    '''Stores all keypresses and then verifies it after pound is pressed'''
    if key == "#":
        if track_keys.store:
            track_keys.state = "Store"
            print("Storing Keys")
            googleSheets.store_password(track_keys.pressed)
            track_keys.store = False
        else: #verify
            if googleSheets.verify_password(track_keys.pressed):
                track_keys.state = "Unlocked"
                print("got in")
            else:
                track_keys.state = "Failed"
                print("didn't get in")
        track_keys.pressed = ""
    else:
        if track_keys.state != "Reset Password":
            track_keys.state = "KeyPressed"
        track_keys.pressed += key
# initialize static variables.
track_keys.pressed = ""
track_keys.store = False
track_keys.state = "Locked"

# Setup Keypad
keypad = rpi_gpio.KeypadFactory().create_keypad(
    keypad = [
        ["1","2","3","A"],
        ["4","5","6","B"],
        ["7","8","9","C"],
        ["*","0","#","D"]
    ],
    row_pins = [4, 14, 15, 17], # BCM numbering; Board numbering is: 7,8,10,11 (see pinout.xyz/)
    col_pins = [18, 27, 22, 23] # BCM numbering; Board numbering is: 12,13,15,16 (see pinout.xyz/)
)
# track_keys will be called each time a keypad button is pressed
keypad.registerKeyPressHandler(track_keys)

def pushbutton_callback(channel):
    print("Enter a password followed by the pound sign (#)")
    track_keys.store = True
    track_keys.pressed = ""
    track_keys.state = "Reset Password"

# Connectes Push button to GPIO 26 (pin 37) with a bouncetime of 500ms so can be reset ever 0.5s
# GPIO.setmode(GPIO.BCM) <- done in rpi
GPIO.setup(26, GPIO.IN, GPIO.PUD_UP)  
GPIO.add_event_detect(26, GPIO.FALLING, callback=pushbutton_callback, bouncetime=500) 

# setup matrix array library...stored in class to organize
class LedThread:
    def __init__(self, interval):
        self.interval = interval
        serial = spi(port=0, device=0, gpio=noop())
        self.device = max7219(serial)
        self._timer = None
        self.is_running = False
        self.timeout = None
        self.start()

    def _run(self):
        self.is_running = False
        self.main_loop()
        self.start()

    def start(self):
        if not self.is_running:
            self._timer = Timer(self.interval, self._run)
            self._timer.start()
            self.is_running = True

    def stop(self):
        self._timer.cancel()
        self.is_running = False

    def main_loop(self):
        if track_keys.state == "KeyPressed":
            self.timeout = None
            self.draw_pressed_key()
        elif track_keys.state == "Unlocked":
            self.draw_open_lock()
        elif track_keys.state == "Locked":
            self.draw_lock()
        elif track_keys.state == "Intruder":
            self.draw_exclamation()
        elif track_keys.state == "Reset Password":
            self.draw_R()
        elif track_keys.state == "Failed":
            for _ in range(6):
                self.draw_no_entry()
                time.sleep(0.1)
                self.device.clear()
                time.sleep(0.1)
            track_keys.state = "Locked"
        else: #state = "Default" keep last state then switch to locked
            pass
            if self.timeout is None: self.timeout = time.time() + 1.0
            if self.timeout < time.time():
                self.timeout = None
                track_keys.state = "Locked"

    def draw_no_entry(self):
        with canvas(self.device) as draw:
            draw.arc([0,0,7,7], start=0,end=360, fill="white")
            draw.line([2,2,5,5], fill="white")  
    def draw_lock(self):
        with canvas(self.device) as draw:
            draw.line([1, 1, 1, 4], fill="white")
            draw.line([6, 1, 6, 4], fill="white")
            draw.line([2, 0, 5, 0], fill="white")
            draw.line([1, 2, 3, 0], fill="white")
            draw.line([6, 2, 4, 0], fill="white")
            draw.rectangle([1,4,6,7], fill="white")
    def draw_open_lock(self):
        with canvas(self.device) as draw:
            draw.arc([1,0,6,6], start=180,end=330, fill="white")
            draw.rectangle([1,4,6,7], fill="white")   
    def draw_exclamation(self):
        with canvas(self.device) as draw:
            draw.rectangle([3,0,4,4], fill="white")
            draw.rectangle([3,6,4,7], fill="white")
    def draw_pressed_key(self):
        with canvas(self.device) as draw:
            key = track_keys.pressed[-1]
            text(draw, (1, 0), key, fill="white", font=LCD_FONT)
            track_keys.state = "Default"
    def draw_R(self):
        with canvas(self.device) as draw:
            text(draw, (1, 0), "R", fill="white", font=SINCLAIR_FONT)

led_thread = LedThread(0.1)

#Ultrasonic sensor with gpio pins 6 & 5
Echo = 6 
Trig = 5
GPIO.setup(Trig, GPIO.OUT)
GPIO.setup(Echo, GPIO.IN)
GPIO.output(Trig, False) #make sure it starts low
print("-Setup inputs-")


# google drive setup
googleSheets = GoogleSheets(
    username = "Pi secure 9001",
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive'],
    jsoncreds = 'SmartSecuritySystem-01d54a73a44d.json',
    clientname = 'SmartSecuritySystem'
)
print("-Setup Google Drive-")


print("-Loaded Program-")


# Main Thread otherwise known as the ultrasonic thread.
try:
    while(True):
        # send a pulse on the ultrasonic sensor
        GPIO.output(Trig, True)
        time.sleep(0.00001)
        GPIO.output(Trig, False)

        while GPIO.input(Echo)==0:
            pulse_start = time.time()
        while GPIO.input(Echo)==1:
            pulse_end = time.time()
        pulse_duration = pulse_end - pulse_start
        distance = pulse_duration * 17150
        print(f"Ultra Distance {distance:0.2f}")
        if distance < 20.0 and track_keys.state is not "Unlocked":
            print("Intruder!!!")
            track_keys.state = "Intruder"
        if distance > 20.0 and track_keys.state is "Intruder":
            track_keys.state = "Default"
        time.sleep(0.1)

except KeyboardInterrupt:
    print(f"\n-Program Closed-")
finally:
    led_thread.stop()
    keypad.cleanup()

# sources #
# link for setting up the max7219 led matrix
# https://raspi.tv/2013/8-x-8-led-array-driven-by-max7219-on-the-raspberry-pi-via-python

# link for setting up the pad4pi lib for the keypad
# https://github.com/brettmclean/pad4pi 

# link for setting up the ultrasonic sensor
# https://thepihut.com/blogs/raspberry-pi-tutorials/hc-sr04-ultrasonic-range-sensor-on-the-raspberry-pi
