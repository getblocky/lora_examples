import signal
import wiringpi
import time

# RF95 CS = GPIO25(Pin 22), IRQ=GPIO4(Pin7), RST=GPIO17(Pin11), LED=N/A OK NodeID=1 @ 433.00MHz
#LED_PIN = 23
RST_PIN = 17
CS_PIN = 25
IRQ_PIN = 4
SPI_WRITE_MASK = 0x80

SPIchannel = 0  # SPI Channel (CE0)
SPIspeed = 1562500  # Clock Speed in Hz (base 400MHz/256)
wiringpi.wiringPiSetupGpio()
wiringpi.wiringPiSPISetup(SPIchannel, SPIspeed)


def twos_complement(input_value, num_bits):
    """
        Calculates a two's complement integer from the given input value's bits
        Copied from wikipedia: https://en.wikipedia.org/wiki/Two's_complement
    """
    mask = 2**(num_bits - 1)
    return -(input_value & mask) + (input_value & ~mask)


# working spiRead function which returns the int value of the returned register
def spi_read(register):
    send_data = str(bytearray([register & ~SPI_WRITE_MASK, 0x0]))
    length, recv_data = wiringpi.wiringPiSPIDataRW(SPIchannel, send_data)
    if length != 2:
        raise ValueError("not enough data returned")
    return ord(recv_data[1])


def spi_burst_read(register, length):
    data = []
    for _ in range(0, length - 1):
        data.append(spi_read(register))
    return data


def spi_write(register, value):
    send_data = str(bytearray([register | SPI_WRITE_MASK, value]))
    length, recv_data = wiringpi.wiringPiSPIDataRW(SPIchannel, send_data)
    return ord(recv_data[1])  # let's return the return value, probably it's the previous one or whatever


class RF95Registers:
    version = 0x42
    irq_flags = 0x12

    # RH_RF95_REG_01_OP_MODE                             0x01
    mode = 0x01

    # RH_RF95_REG_06_FRF_MSB                             0x06
    rf_carrier_frequency_msb = 0x06
    # RH_RF95_REG_07_FRF_MID                             0x07
    rf_carrier_frequency_mid = 0x07
    # RH_RF95_REG_08_FRF_LSB                             0x08
    rf_carrier_frequency_lsb = 0x08

    # RH_RF95_REG_0D_FIFO_ADDR_PTR                       0x0d
    fifo_spi_address_pointer = 0x0d
    # RH_RF95_REG_0E_FIFO_TX_BASE_ADDR                   0x0e
    fifo_tx_base_addr = 0x0e
    # RH_RF95_REG_0F_FIFO_RX_BASE_ADDR                   0x0f
    fifo_rx_base_addr = 0x0f

    # RH_RF95_REG_10_FIFO_RX_CURRENT_ADDR                0x10
    # Start address (in data buffer) of last packet received
    fifo_last_packet_address = 0x10

    # RH_RF95_REG_13_RX_NB_BYTES FifoNbRxBytes           0x13
    last_packet_payload_bytes = 0x13
    # RH_RF95_REG_14_RX_HEADER_CNT_VALUE_MSB             0x14
    valid_header_count_msb = 0x14
    # RH_RF95_REG_15_RX_HEADER_CNT_VALUE_LSB             0x15
    valid_header_count_lsb = 0x15
    # RH_RF95_REG_16_RX_PACKET_CNT_VALUE_MSB             0x16
    valid_packet_count_msb = 0x16
    # RH_RF95_REG_17_RX_PACKET_CNT_VALUE_LSB             0x17
    valid_packet_count_lsb = 0x17

    # RH_RF95_REG_19_PKT_SNR_VALUE                       0x19
    last_packet_snr = 0x19
    # RH_RF95_REG_1A_PKT_RSSI_VALUE                      0x1a
    last_packet_rssi = 0x1a
    # RH_RF95_REG_1D_MODEM_CONFIG1                       0x1d
    modem_config_1 = 0x1d
    # RH_RF95_REG_1E_MODEM_CONFIG2                       0x1e
    modem_config_2 = 0x1e
    # RH_RF95_REG_40_DIO_MAPPING1                        0x40
    dio_mapping_g0 = 0x40


class RF95Modes:
    # RH_RF95_MODE_SLEEP                            0x00
    sleep = 0x00
    # RH_RF95_MODE_STDBY                            0x01
    standby = 0x01
    # RH_RF95_MODE_RXCONTINUOUS                     0x05
    rx_continous = 0x05
    # RH_RF95_LONG_RANGE_MODE                       0x80
    loRa = 0x80


class RF95Bandwidth:
    # the bits are 7-4, so we need to shift them to the left
    # Signal bandwidth:
    # 0000 7.8 kHz
    # 0001 10.4 kHz
    # 0010 15.6 kHz
    # 0011 20.8kHz
    # 0100 31.25 kHz
    # 0101 41.7 kHz
    # 0110 62.5 kHz
    # 0111 125 kHz
    # 1000 250 kHz
    # 1001 500 kHz
    # other values reserved
    # In the lower band (169MHz), signal bandwidths 8&9 are not supported)
    bw_7_8_khz = 0x0 << 4
    bw_10_4_khz = 0x01 << 4
    bw_15_6_khz = 0x02 << 4
    bw_20_8_khz = 0x03 << 4
    bw_31_25_khz = 0x04 << 4
    bw_41_7_khz = 0x05 << 4
    bw_62_5_khz = 0x06 << 4
    bw_125_khz = 0x07 << 4
    bw_250_khz = 0x08 << 4
    bw_500_khz = 0x09 << 4


class RF95CodingRate:
    # bits 3-1, so we need to shift as well
    # Error coding rate
    # 001   4/5
    # 010   4/6
    # 011   4/7
    # 100   4/8
    # All other values reserved
    # In implicit header mode should be set on receiver to determine expected coding rate. See Section 4.1.1.3
    cr_4_5 = 0x01 << 1
    cr_4_6 = 0x02 << 1
    cr_4_7 = 0x03 << 1
    cr_4_8 = 0x04 << 1


class RF95HeaderMode:
    explicit = 0x00
    implicit = 0x01


class RF95SpreadingFactor:
    # bits 7 - 4, so we need to shift
    # Spreading Factor rate (expressed as a base-2 logarithm)
    # 6 64 chips / symbol
    # 7 128 chips / symbol
    # 8 256 chips / symbol
    # 9 512 chips / symbol
    # 10 1024 chips / symbol
    # 11 2048 chips / symbol
    # 12 4096 chips / symbol
    # other values reserved.
    sf_6 = 6 << 4  # 64
    sf_7 = 7 << 4  # 128
    sf_8 = 8 << 4  # 256
    sf_9 = 9 << 4  # 512
    sf_10 = 10 << 4  # 1024
    sf_11 = 11 << 4  # 2048
    sf_12 = 12 << 4  # 4096


class ModemConfig:

    register_1d = None
    register_1e = None

    def __init__(self,
                 bandwidth=RF95Bandwidth.bw_125_khz,
                 coding_rate=RF95CodingRate.cr_4_5,
                 header_mode=RF95HeaderMode.explicit,
                 spreading_factor=RF95SpreadingFactor.sf_7,
                 crc_enabled=True):
        self.register_1d = bandwidth | coding_rate | header_mode
        self.register_1e = spreading_factor | 0x01 << 2 if crc_enabled else 0x00 << 2

# Bw125Cr45Sf128 = 0,	   ///< Bw = 125 kHz, Cr = 4/5, Sf = 128chips/symbol, CRC on. Default medium range
# Bw500Cr45Sf128,	           ///< Bw = 500 kHz, Cr = 4/5, Sf = 128chips/symbol, CRC on. Fast+short range
# Bw31_25Cr48Sf512,	   ///< Bw = 31.25 kHz, Cr = 4/8, Sf = 512chips/symbol, CRC on. Slow+long range
# Bw125Cr48Sf4096,           ///< Bw = 125 kHz, Cr = 4/8, Sf = 4096chips/symbol, CRC on. Slow+long range
DefaultModemConfigs = {
    'default': ModemConfig(),
    'fast_short': ModemConfig(bandwidth=RF95Bandwidth.bw_500_khz),
    'slow_long_1': ModemConfig(bandwidth=RF95Bandwidth.bw_31_25_khz,
                               coding_rate=RF95CodingRate.cr_4_8,
                               spreading_factor=RF95SpreadingFactor.sf_9),
    'slow_long_2': ModemConfig(bandwidth=RF95Bandwidth.bw_125_khz,
                               coding_rate=RF95CodingRate.cr_4_8,
                               spreading_factor=RF95SpreadingFactor.sf_12)
}


class RF95Interrupt:

    def __init__(self, value):
        """

        :param value: the contents of the interrupt register
        :type value: int
        """
        self.value = value

    def valid(self):
        # strangely, the second received interrupt is empty
        return self.value != 0x0

    def timeout(self):
        # RH_RF95_RX_TIMEOUT_MASK
        return self.value & 0x80 == 0x80

    def rx_done(self):
        return self.value & 0x40 == 0x40

    def payload_crc_error(self):
        return self.value & 0x20 == 0x20

    def valid_header(self):
        return self.value & 0x10 == 0x10

    def tx_done(self):
        return self.value & 0x08 == 0x08

    def cad_done(self):
        return self.value & 0x04 == 0x04

    def fhss_channel_change(self):
        return self.value & 0x02 == 0x02

    def cad_detected(self):
        return self.value & 0x01 == 0x01

    def __str__(self):
        result = []
        if not self.valid():
            result.append("INVALID_INTERRUPT")
        if self.timeout():
            result.append("TIMEOUT")
        if self.rx_done():
            result.append("RX_DONE")
        if self.payload_crc_error():
            result.append("CRC_ERROR")
        if self.valid_header():
            result.append("VALID_HEADER")
        if self.tx_done():
            result.append("TX_DONE")
        if self.cad_done():
            result.append("CAD_DONE")
        if self.fhss_channel_change():
            result.append("FHSS_CHANNEL_CHANGE")
        if self.cad_detected():
            result.append("CAD_DETECTED")
        if self.cad_done() and not self.cad_detected():
            result.append("CAD_CLEAR")

        return " | ".join(result)


class LoRaPacketHeader:

    def __init__(self, data):
        """
        :param data: the header (4 bytes)
        :type data: []int
        """
        if len(data) != 4:
            raise ValueError("header needs to be 4 bytes")

        self.source, self.dest, self.id, self.flags = data

    def __str__(self):
        return "LoRaPacketHeader(source={}, dest={}, id={}, flags={})".format(self.source, self.dest, self.id, self.flags)


def gpio_callback():
    print "GPIO_CALLBACK!", time.time()
    # wiringpi.digitalWrite(LED_PIN, 1)
    irq_flags = spi_read(RF95Registers.irq_flags)
    result = RF95Interrupt(irq_flags)
    print result

    if result.rx_done():
        # // Have received a packet
        # uint8_t len = spiRead(RH_RF95_REG_13_RX_NB_BYTES);
        packet_length = spi_read(RF95Registers.last_packet_payload_bytes)
        print "last packet length", packet_length

        # // Reset the fifo read ptr to the beginning of the packet
        # spiWrite(RH_RF95_REG_0D_FIFO_ADDR_PTR, spiRead(RH_RF95_REG_10_FIFO_RX_CURRENT_ADDR));
        last_packet_buffer_address = spi_read(RF95Registers.fifo_last_packet_address)
        print "last packet address", last_packet_buffer_address
        spi_write(RF95Registers.fifo_spi_address_pointer, last_packet_buffer_address)
        print "reading data",
        data = spi_burst_read(0x00, packet_length)
        print data
        header = LoRaPacketHeader(data[:4])
        print header
        print "data", "".join([chr(x) for x in data[4:]])

        print "valid headers", (spi_read(RF95Registers.valid_header_count_msb) << 8 | spi_read(RF95Registers.valid_header_count_lsb))
        print "valid packets", (spi_read(RF95Registers.valid_packet_count_msb) << 8 | spi_read(RF95Registers.valid_packet_count_lsb))
        # Estimation of SNR on last packet received.In two's compliment format mutiplied(sic!) by 4.
        print "last packet SNR", twos_complement(spi_read(RF95Registers.last_packet_snr) & ~0x80, 7) / 4
        print "last packet RSSI", -137 + spi_read(RF95Registers.last_packet_rssi)

    # spiWrite(RH_RF95_REG_12_IRQ_FLAGS, 0xff); // Clear all IRQ flags
    spi_write(0x12, 0xff)
    #wiringpi.digitalWrite(LED_PIN, 0)


def shutdown(signum, frame):
    print "shutting down"
    reset()
    exit(0)


def reset():
    print "Resetting RF95"
    wiringpi.digitalWrite(RST_PIN, 0)
    time.sleep(0.150)
    wiringpi.digitalWrite(RST_PIN, 1)
    time.sleep(0.1)

signal.signal(signal.SIGINT, shutdown)

wiringpi.pinMode(IRQ_PIN, wiringpi.GPIO.INPUT)
wiringpi.pullUpDnControl(IRQ_PIN, wiringpi.GPIO.PUD_DOWN)
wiringpi.pinMode(RST_PIN, wiringpi.GPIO.OUTPUT)
wiringpi.pinMode(LED_PIN, wiringpi.GPIO.OUTPUT)

wiringpi.wiringPiISR(IRQ_PIN, wiringpi.GPIO.INT_EDGE_RISING, gpio_callback)

reset()

print "SPI:",
version = spi_read(RF95Registers.version)
if version == 0x12:
    print "found: SX1276 RF95/96"
else:
    print "unknown device"
    print version
    exit(1)

readMode = spi_read(RF95Registers.mode)
print "current mode", hex(readMode), bin(readMode)

print "setting mode to sleep and LoRa"
spi_write(RF95Registers.mode, RF95Modes.sleep | RF95Modes.loRa)
time.sleep(0.1)

print "verifying mode"
readMode = spi_read(RF95Registers.mode)
print hex(readMode), bin(readMode)
print readMode == (RF95Modes.sleep | RF95Modes.loRa)


def get_frequency():
    msb = spi_read(RF95Registers.rf_carrier_frequency_msb)
    mid = spi_read(RF95Registers.rf_carrier_frequency_mid)
    lsb = spi_read(RF95Registers.rf_carrier_frequency_lsb)
    return (msb << 16 | mid << 8 | lsb) * (32e6 / 2**19)  # freq * 32 mhz / 2^19


def set_frequency(frequency):
    fstep = 32e6 / 2 ** 19
    print "fstep", fstep
    frf = int((frequency * 1000000) / fstep)
    print "frf", frf
    print "msb", (frf >> 16) & 0xff
    print "mid", (frf >> 8) & 0xff
    print "lsb", frf & 0xff

    spi_write(RF95Registers.rf_carrier_frequency_msb, (frf >> 16) & 0xff)
    spi_write(RF95Registers.rf_carrier_frequency_mid, (frf >> 8) & 0xff)
    spi_write(RF95Registers.rf_carrier_frequency_lsb, frf & 0xff)


def set_mode_idle():
    spi_write(RF95Registers.mode, RF95Modes.standby)


def set_modem_config(config):
    """
    :type config: ModemConfig
    """
    # spiWrite(RH_RF95_REG_1D_MODEM_CONFIG1, config->reg_1d);
    # spiWrite(RH_RF95_REG_1E_MODEM_CONFIG2, config->reg_1e);
    # spiWrite(RH_RF95_REG_26_MODEM_CONFIG3, config->reg_26);
    print "pre config"
    print spi_read(RF95Registers.modem_config_1)
    print spi_read(RF95Registers.modem_config_2)
    print "setting modem config"
    spi_write(RF95Registers.modem_config_1, config.register_1d)
    spi_write(RF95Registers.modem_config_2, config.register_1e)
    print "post config"
    print spi_read(RF95Registers.modem_config_1)
    print spi_read(RF95Registers.modem_config_2)

print "set fifo addrs"
print spi_read(RF95Registers.fifo_rx_base_addr)
print spi_read(RF95Registers.fifo_tx_base_addr)
spi_write(RF95Registers.fifo_rx_base_addr, 0)
spi_write(RF95Registers.fifo_tx_base_addr, 0)
print spi_read(RF95Registers.fifo_rx_base_addr)
print spi_read(RF95Registers.fifo_tx_base_addr)

print "read frequency"
print get_frequency()

print "set frequency"
freq = 868
set_frequency(freq)

print "verify frequency"
print get_frequency()

set_modem_config(DefaultModemConfigs['default'])

print "setting mode to RX cont"
spi_write(RF95Registers.mode, RF95Modes.rx_continous)
spi_write(RF95Registers.dio_mapping_g0, 0x00)  # RxDone

print "waiting"
signal.pause()