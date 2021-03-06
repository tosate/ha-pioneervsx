"""Support for Pioneer Network Receivers."""
import logging
import telnetlib
import time
import voluptuous as vol

from homeassistant.components.media_player import (
    MediaPlayerDevice, PLATFORM_SCHEMA)
from homeassistant.components.media_player.const import (
    SUPPORT_PAUSE, SUPPORT_PLAY, SUPPORT_SELECT_SOURCE,
    SUPPORT_TURN_OFF, SUPPORT_TURN_ON, SUPPORT_VOLUME_MUTE, SUPPORT_VOLUME_SET)
from homeassistant.const import (
    CONF_HOST, CONF_NAME, CONF_PORT, CONF_TIMEOUT, STATE_OFF, STATE_ON)
import homeassistant.helpers.config_validation as cv


_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = 'Pioneer AVR'
DEFAULT_PORT = 23   # telnet default. Some Pioneer AVRs use 8102
DEFAULT_TIMEOUT = None

CONF_ENABLED_SOURCES_ONLY   = 'enabled_sources_only'
CONF_DISABLED_SOURCES   = 'disabled_sources'

SUPPORT_PIONEER = SUPPORT_PAUSE | SUPPORT_VOLUME_SET | SUPPORT_VOLUME_MUTE | \
                  SUPPORT_TURN_ON | SUPPORT_TURN_OFF | \
                  SUPPORT_SELECT_SOURCE | SUPPORT_PLAY

MAX_VOLUME = 185
MAX_SOURCE_NUMBERS = 60

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_HOST): cv.string,
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
    vol.Optional(CONF_ENABLED_SOURCES_ONLY, default=True): cv.boolean,    
    vol.Optional(CONF_DISABLED_SOURCES): [cv.string],    
    vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): cv.socket_timeout,
})


def setup_platform(hass, config, add_entities, discovery_info=None):
    """Set up the Pioneer platform."""
    pioneer = PioneerDevice(
        config.get(CONF_NAME), config.get(CONF_HOST), config.get(CONF_PORT),
        config.get(CONF_TIMEOUT),
        config.get(CONF_ENABLED_SOURCES_ONLY), config.get(CONF_DISABLED_SOURCES))

    if pioneer.update():
        add_entities([pioneer])


class PioneerDevice(MediaPlayerDevice):
    """Representation of a Pioneer device."""

    def __init__(self, name, host, port, timeout, enabled_sources_only, disabled_sources):
        """Initialize the Pioneer device."""
        self._name = name
        self._host = host
        self._port = port
        self._timeout = timeout
        self._pwstate = 'PWR1'
        self._volume = 0
        self._muted = False
        self._selected_source = ''
        self._source_name_to_number = {}
        self._source_number_to_name = {}
        self._source_enabled = {}
        self._enabled_sources_only = enabled_sources_only
        self._disabled_source_list = {}
        if disabled_sources:
            self._disabled_source_list = disabled_sources        

    @classmethod
    def telnet_request(cls, telnet, command, expected_prefix):
        """Execute `command` and return the response."""
        try:
            telnet.write(command.encode("ASCII") + b"\r")
        except telnetlib.socket.timeout:
            _LOGGER.debug("Pioneer command %s timed out", command)
            return None
        return cls.read_response(telnet, expected_prefix)

    @classmethod
    def read_response(cls, telnet, expected_prefix):
        # The receiver will randomly send state change updates, make sure
        # we get the response we are looking for
        for _ in range(3):
            result = telnet.read_until(b"\r\n", timeout=0.2).decode("ASCII") \
                .strip()
            if result.startswith(expected_prefix):
                return result
        return None

    def telnet_wakeup(self, telnet):
        telnet.write(b"\r")
        time.sleep(0.1)
        telnet.write(b"\r")
        _LOGGER.debug("Pioneer sent wakeup command")
        result = telnet.read_until(b"R\r", timeout=0.2).decode("ASCII").strip()
        _LOGGER.debug("Pioneer ready: %s", result)
        #if not result.startswith("R"):
        #    raise Exception('Pioneer not ready, after wakeup')
        #telnet.read_very_eager()  # skip response

    def telnet_command(self, command, expected_prefix):
        """Establish a telnet connection and sends command."""
        try:
            try:
                telnet = telnetlib.Telnet(
                    self._host, self._port, self._timeout)
            except (ConnectionRefusedError, OSError):
                _LOGGER.warning("Pioneer %s refused connection", self._name)
                return

            self.telnet_wakeup(telnet)

            cmdAscii = command.encode("ASCII")
            telnet.write(cmdAscii + b"\r")
            _LOGGER.debug("Sent command: %s", cmdAscii)

            if expected_prefix:
                response = self.read_response(telnet, expected_prefix)
                _LOGGER.debug("Command Response: %s", response)

                self.updateResponse(expected_prefix, response)
                #time.sleep(0.1)
                #if (self.hass and self.async_schedule_update_ha_state):
                #    self.async_schedule_update_ha_state(force_refresh=False)

            telnet.close()
        except telnetlib.socket.timeout:
            _LOGGER.debug(
                "Pioneer %s command %s timed out", self._name, command)

    def processRequest(self, telnet, request, expected_response):
        response = self.telnet_request(telnet, request, expected_response)
        self.updateResponse(expected_response, response)

    def updateResponse(self, prefix, value):
        switcher = {
            "PWR": self.setPower,
            "VOL": self.setVolume,
            "MUT": self.setMute,
            "FN": self.setSource
        }
        # Get the function from switcher dictionary
        responseAction = switcher.get(prefix, None)
        
        if responseAction is None:
            _LOGGER.warn("Prefix handler not implemented: %s", prefix)          
            return

        # Execute the function
        responseAction(value)
    
    def setPower(self, pwstate):
        if pwstate:
            self._pwstate = pwstate

    def setVolume(self, volume_str):
        self._volume = int(volume_str[3:]) / MAX_VOLUME if volume_str else None
    
    def setMute(self, muted_value):
        self._muted = (muted_value == "MUT0") if muted_value else None 

    def buildSourceMap(self, telnet):
        if not self._source_name_to_number:
            for i in range(MAX_SOURCE_NUMBERS):
                result = self.telnet_request(
                    telnet, "?RGB" + str(i).zfill(2), "RGB")

                if not result:
                    continue

                source_enabled = (result[5]=="1")
                source_name = result[6:]
                source_number = str(i).zfill(2)

                self._source_enabled[source_number] = source_enabled
                self._source_name_to_number[source_name] = source_number
                self._source_number_to_name[source_number] = source_name

    def setSource(self, source_number):
        if source_number:
            self._selected_source = self._source_number_to_name \
                .get(source_number[2:])
        else:
            self._selected_source = None

    def update(self):
        """Get the latest details from the device."""
        try:
            telnet = telnetlib.Telnet(self._host, self._port, self._timeout)
        except (ConnectionRefusedError, OSError):
            _LOGGER.warning("Pioneer %s refused connection", self._name)
            return False

        self.telnet_wakeup(telnet)

        # Build the source name dictionaries if necessary
        self.buildSourceMap(telnet)   

        self.processRequest(telnet,"?P", "PWR")
        self.processRequest(telnet,"?V", "VOL")
        self.processRequest(telnet,"?M", "MUT")
        self.processRequest(telnet,"?F", "FN")

        telnet.close()
        return True

    @property
    def name(self):
        """Return the name of the device."""
        return self._name

    @property
    def state(self):
        """Return the state of the device."""
        if self._pwstate == "PWR1":
            return STATE_OFF
        if self._pwstate == "PWR0":
            return STATE_ON

        return None

    @property
    def volume_level(self):
        """Volume level of the media player (0..1)."""
        return self._volume

    @property
    def is_volume_muted(self):
        """Boolean if volume is currently muted."""
        return self._muted

    @property
    def supported_features(self):
        """Flag media player features that are supported."""
        return SUPPORT_PIONEER

    @property
    def source(self):
        """Return the current input source."""
        return self._selected_source

    @property
    def source_list(self):
        """List of available input sources."""
        if ((len(self._disabled_source_list) or self._enabled_sources_only) and len(self._source_name_to_number)):
            enabled_sources = {}
            for name, number in self._source_name_to_number.items():
                if (name not in self._disabled_source_list 
                    and (not self._enabled_sources_only 
                        or self._source_enabled[number])):
                    enabled_sources[name] = number
            return list(enabled_sources.keys())

        return list(self._source_name_to_number.keys())

    @property
    def media_title(self):
        """Title of current playing media."""
        return self._selected_source

    def turn_off(self):
        """Turn off media player."""
        self.telnet_command("PF", "PWR")

    def volume_up(self):
        """Volume up media player."""
        self.telnet_command("VU", "VOL")

    def volume_down(self):
        """Volume down media player."""
        self.telnet_command("VD", "VOL")

    def set_volume_level(self, volume):
        """Set volume level, range 0..1."""
        # 60dB max
        self.telnet_command(str(round(volume * MAX_VOLUME)).zfill(3) + "VL", "VOL")

    def mute_volume(self, mute):
        """Mute (true) or unmute (false) media player."""
        self.telnet_command("MO" if mute else "MF", "MUT")

    def turn_on(self):
        """Turn the media player on."""
        self.telnet_command("PO", "PWR")

    def select_source(self, source):
        """Select input source."""
        if source in self._source_name_to_number:
            self.telnet_command(self._source_name_to_number.get(source) + "FN", "FN")
        else:
            _LOGGER.error("Unknown input '%s'", source)