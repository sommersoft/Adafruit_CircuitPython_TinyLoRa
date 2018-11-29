# Copyright 2015, 2016 Ideetron B.V.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
#
# Modified by Brent Rubell for Adafruit Industries.
"""
`Adafruit_TinyLoRa`
====================================================
CircuitPython LoRaWAN implementation for use with
The Things Network.

* Author(s): adafruit

Implementation Notes
--------------------

**Hardware:**

* `Adafruit RFM95W LoRa Radio Transceiver Breakout <https://www.adafruit.com/product/3072>`_

**Software and Dependencies:**

* Adafruit CircuitPython firmware for the supported boards:
  https://github.com/adafruit/circuitpython/releases

* Adafruit's Bus Device library: https://github.com/adafruit/Adafruit_CircuitPython_BusDevice
"""

import time
from random import randint
from micropython import const
import adafruit_bus_device.spi_device as spi_device
from adafruit_tinylora_encryption import AES

__version__ = "0.0.0-auto.0"
__repo__ = "https://github.com/adafruit/Adafruit_CircuitPython_TinyLoRa.git"

# RFM Module Settings
_MODE_SLEEP = const(0x00)
_MODE_LORA = const(0x80)
_MODE_STDBY = const(0x01)
_MODE_TX = const(0x83)
_TRANSMIT_DIRECTION_UP = const(0x00)
# RFM Registers
_REG_PA_CONFIG = const(0x09)
_REG_PREAMBLE_MSB = const(0x20)
_REG_PREAMBLE_LSB = const(0x21)
_REG_FRF_MSB = const(0x06)
_REG_FRF_MID = const(0x07)
_REG_FRF_LSB = const(0x08)
_REG_FEI_LSB = const(0x1E)
_REG_FEI_MSB = const(0x1D)
_REG_MODEM_CONFIG = const(0x26)
_REG_PAYLOAD_LENGTH = const(0x22)
_REG_FIFO_POINTER = const(0x0d)
_REG_FIFO_BASE_ADDR = const(0x80)
_REG_OPERATING_MODE = const(0x01)
_REG_VERSION = const(0x42)
# Freq synth step
_FSTEP = (32000000.0 / 524288)

class TTN:
    """TTN Class
    """
    def __init__(self, dev_address, net_key, app_key, country='US'):
        """Interface for TheThingsNetwork
        :param bytearray dev_address: TTN Device Address.
        :param bytearray net_key: TTN Network Key.
        :param bytearray app_key: TTN Application Key.
        :param string country: TTN Region.
        """
        self.dev_addr = dev_address
        self.net_key = net_key
        self.app_key = app_key
        self.region = country

    @property
    def country(self):
        """Returns the TTN Frequency Country.
        """
        return self.region

    @property
    def device_address(self):
        """Returns the TTN Device Address.
        """
        return self.dev_addr

    @property
    def application_key(self):
        """Returns the TTN Application Key.
        """
        return self.app_key

    @property
    def network_key(self):
        """Returns the TTN Network Key.
        """
        return self.net_key


# pylint: disable=too-many-instance-attributes
class TinyLoRa:
    """TinyLoRa Interface
    """
    # SPI Write Buffer
    _BUFFER = bytearray(2)

    # pylint: disable=too-many-arguments
    def __init__(self, spi, cs, irq, ttn_config, channel=None):
        """Interface for a HopeRF RFM95/6/7/8(w) radio module. Sets module up for sending to
        The Things Network.

        :param ~busio.SPI spi: The SPI bus the device is on
        :param ~digitalio.DigitalInOut cs: Chip select pin (RFM_NSS)
        :param ~digitalio.DigitalInOut irq: RFM's DIO0 Pin (RFM_DIO0)
        :param TTN ttn_config: TTN Configuration.
        :param int channel: Frequency Channel.
        """
        self._irq = irq
        # Set up SPI Device on Mode 0
        self._device = spi_device.SPIDevice(spi, cs, baudrate=4000000,
                                            polarity=0, phase=0)
        # Set Frequency registers
        self._rfm_msb = None
        self._rfm_mid = None
        self._rfm_lsb = None
        # Set datarate registers
        self._sf = None
        self._bw = None
        self._modemcfg = None
        self.set_datarate("SF7BW125")
        # Set regional frequency plan
        if 'US' in ttn_config.country:
            from ttn_usa import TTN_FREQS
            self._frequencies = TTN_FREQS
        elif ttn_config.country == 'AS':
            from ttn_as import TTN_FREQS
            self._frequencies = TTN_FREQS
        elif ttn_config.country == 'AU':
            from ttn_au import TTN_FREQS
            self._frequencies = TTN_FREQS
        elif ttn_config.country == 'EU':
            from ttn_eu import TTN_FREQS
            self._frequencies = TTN_FREQS
        else:
            print("Country Code Incorrect/Unsupported")
        # Set Channel Number
        self._channel = channel
        self._tx_random = randint(0, 7)
        if self._channel is not None:
            # set single channel
            self.set_channel(self._channel)
        # Init FrameCounter
        self.frame_counter = 0
        # Verify the version of the RFM module
        self._version = self._read_u8(0x42)
        if self._version != 18:
            print("Error Detecting RFM95W.")
            print(" Check your wiring.")
        # Set RFM to Sleep Mode
        self._write_u8(0x01, _MODE_SLEEP)
        # Set RFM to LoRa mode
        self._write_u8(0x01, _MODE_LORA)
        # Set Max. Power
        self._write_u8(0x09, 0xFF)
        # Set RX Timeout
        self._write_u8(0x1F, 0x25)
        # Preamble Length = 8
        # Setup preamble (0x0008 + 4)
        self._write_u8(_REG_PREAMBLE_MSB, 0x00)
        self._write_u8(_REG_PREAMBLE_LSB, 0x08)
        # Low datarate optimization off AGC auto on
        self._write_u8(0x26, 0x0C)
        # Set LoRa sync word
        self._write_u8(0x39, 0x34)
        # Set IQ to normal values
        self._write_u8(0x33, 0x27)
        self._write_u8(0x3B, 0x1D)
        # Set FIFO pointers
        # TX base adress
        self._write_u8(0x0E, 0x80)
        # Rx base adress
        self._write_u8(0x0F, 0x00)
        # Give the lora object ttn configuration
        self._ttn_config = ttn_config

    def send_data(self, data, data_length, frame_counter):
        """Function to assemble and send data
           :param data: data to send
           :param data_length: length of data to send
           :param frame_counter: frame counter variable, declared in code.py
        """
        # data packet
        enc_data = bytearray(data_length)
        lora_pkt = bytearray(64)
        # copy bytearray into bytearray for encryption
        for i in range(0, data_length):
            enc_data[i] = data[i]
        # encrypt data (enc_data is overwritten in this function)
        self.frame_counter = frame_counter
        aes = AES(self._ttn_config.device_address, self._ttn_config.app_key,
                  self._ttn_config.network_key, self.frame_counter)
        enc_data = aes.encrypt(enc_data)
        # append preamble to packet
        lora_pkt[0] = const(0x40)
        lora_pkt[1] = self._ttn_config.device_address[3]
        lora_pkt[2] = self._ttn_config.device_address[2]
        lora_pkt[3] = self._ttn_config.device_address[1]
        lora_pkt[4] = self._ttn_config.device_address[0]
        lora_pkt[5] = 0
        lora_pkt[6] = frame_counter & 0x00FF
        lora_pkt[7] = (frame_counter >> 8) & 0x00FF
        lora_pkt[8] = 0x01
        # set length of LoRa packet
        lora_pkt_len = 9
        # load encrypted data into lora_pkt
        for i in range(0, data_length):
            lora_pkt[lora_pkt_len + i] = enc_data[i]
        # recalculate packet length
        lora_pkt_len = lora_pkt_len + data_length
        # Calculate MIC
        mic = bytearray(4)
        mic = aes.calculate_mic(lora_pkt, lora_pkt_len, mic)
        # load mic in package
        for i in range(0, 4):
            lora_pkt[i + lora_pkt_len] = mic[i]
        # recalculate packet length (add MIC length)
        lora_pkt_len += 4
        self.send_packet(lora_pkt, lora_pkt_len)

    def send_packet(self, lora_packet, packet_length):
        """Sends a LoRa packet using the RFM Module
          :param bytearray lora_packet: assembled LoRa packet from send_data
          :param int packet_length: length of LoRa packet to send
        """
        # Set RFM to standby
        self._write_u8(_MODE_STDBY, 0x81)
        # wait for RFM to enter standby mode
        time.sleep(0.01)
        # switch interrupt to txdone
        self._write_u8(0x40, 0x40)
        # check for multi-channel configuration
        if self._channel is None:
            self._tx_random = randint(0, 7)
            self._rfm_lsb = self._frequencies[self._tx_random][2]
            self._rfm_mid = self._frequencies[self._tx_random][1]
            self._rfm_msb = self._frequencies[self._tx_random][0]
        # write to RFM channel registers...
        self._write_u8(_REG_FRF_MSB, self._rfm_msb)
        self._write_u8(_REG_FRF_MID, self._rfm_mid)
        self._write_u8(_REG_FRF_LSB, self._rfm_lsb)
        # set RFM datarate
        self._write_u8(_REG_FEI_LSB, self._sf)
        self._write_u8(_REG_FEI_MSB, self._bw)
        self._write_u8(_REG_MODEM_CONFIG, self._modemcfg)
        # set RegPayloadLength
        self._write_u8(_REG_PAYLOAD_LENGTH, packet_length)
        # initalize FIFO pointer to base address for TX
        self._write_u8(_REG_FIFO_POINTER, _REG_FIFO_BASE_ADDR)
        # fill the FIFO buffer with the LoRa payload
        k = 0  # ptr
        i = 0
        while i < packet_length:
            self._write_u8(0x00, lora_packet[k])
            i += 1
            k += 1
        # switch RFM to TX operating mode
        self._write_u8(_REG_OPERATING_MODE, _MODE_TX)
        # wait for TxDone IRQ
        print('Sending packet')
        send_attempt = 0
        while not self._irq.value and send_attempt < 15:
            # waiting for TxDone
            time.sleep(1)
            send_attempt += 1
        # switch RFM to sleep operating mode
        print('Packet Sent!')
        self._write_u8(_REG_OPERATING_MODE, _MODE_SLEEP)

    def set_datarate(self, datarate):
        """Sets the RFM Datarate
        :param datarate: Bandwidth and Frequency Plan
        """
        if datarate == 'SF7BW125':
            self._sf = 0x74
            self._bw = 0x72
            self._modemcfg = 0x04
        elif datarate == 'SF7BW250':
            self._sf = 0x74
            self._bw = 0x82
            self._modemcfg = 0x04
        elif datarate == 'SF8BW125':
            self._sf = 0x84
            self._bw = 0x72
            self._modemcfg = 0x04
        elif datarate == 'SF9BW125':
            self._sf = 0x94
            self._bw = 0x72
            self._modemcfg = 0x04
        elif datarate == 'SF10BW125':
            self._sf = 0xA4
            self._bw = 0x72
            self._modemcfg = 0x04
        elif datarate == 'SF11BW125':
            self._sf = 0xB4
            self._bw = 0x72
            self._modemcfg = 0x0C
        elif datarate == 'SF12BW125':
            self._sf = 0xC4
            self._bw = 0x72
            self._modemcfg = 0x0C
        else:
            raise TypeError("Invalid Datarate.")

    def set_channel(self, channel):
        """Returns the RFM Channel (if single-channel)
        :param int channel: Transmit Channel (0 through 7).
        """
        if self._channel is not None:
            if channel == 0:
                self._rfm_lsb = self._frequencies[0][2]
                self._rfm_mid = self._frequencies[0][1]
                self._rfm_msb = self._frequencies[0][0]
            elif channel == 1:
                self._rfm_lsb = self._frequencies[1][2]
                self._rfm_mid = self._frequencies[1][1]
                self._rfm_msb = self._frequencies[1][0]
            elif channel == 2:
                self._rfm_lsb = self._frequencies[2][2]
                self._rfm_mid = self._frequencies[2][1]
                self._rfm_msb = self._frequencies[2][0]
            elif channel == 3:
                self._rfm_lsb = self._frequencies[3][2]
                self._rfm_mid = self._frequencies[3][1]
                self._rfm_msb = self._frequencies[3][0]
            elif channel == 4:
                self._rfm_lsb = self._frequencies[4][2]
                self._rfm_mid = self._frequencies[4][1]
                self._rfm_msb = self._frequencies[4][0]
            elif channel == 5:
                self._rfm_lsb = self._frequencies[5][2]
                self._rfm_mid = self._frequencies[5][1]
                self._rfm_msb = self._frequencies[5][0]
            elif channel == 6:
                self._rfm_lsb = self._frequencies[6][2]
                self._rfm_mid = self._frequencies[6][1]
                self._rfm_msb = self._frequencies[6][0]
            elif channel == 7:
                self._rfm_lsb = self._frequencies[7][2]
                self._rfm_mid = self._frequencies[7][1]
                self._rfm_msb = self._frequencies[7][0]
        else:
            raise AttributeError("Can not set Multi-Channel Channel.")

    def _read_into(self, address, buf, length=None):
        """Read a number of bytes from the specified address into the
        provided buffer. If length is not specified (default) the entire buffer
        will be filled.
        :param bytearray address: Register Address.
        :param bytearray buf: Data Buffer for bytes.
        :param int length: Buffer length.
        """
        if length is None:
            length = len(buf)
        with self._device as device:
            # Strip out top bit to set 0 value (read).
            self._BUFFER[0] = address & 0x7F
            device.write(self._BUFFER, end=1)
            device.readinto(buf, end=length)

    def _read_u8(self, address):
        """Read a single byte from the provided address and return it.
        :param bytearray address: Register Address.
        """
        self._read_into(address, self._BUFFER, length=1)
        return self._BUFFER[0]

    def _write_u8(self, address, val):
        """Writes to the RFM register given an address and data.
        :param bytearray address: Register Address.
        :param val: Data to write.
        """
        with self._device as device:
            self._BUFFER[0] = (address | 0x80)  # MSB 1 to Write
            self._BUFFER[1] = val
            device.write(self._BUFFER, end=2)
