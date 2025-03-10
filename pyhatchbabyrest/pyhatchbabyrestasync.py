import asyncio
from typing import Optional
from typing import Union
from datetime import datetime, UTC

from bleak import BleakClient
from bleak import BleakScanner
from bleak.backends.device import BLEDevice

from .constants import BT_MANUFACTURER_ID
from .constants import CHAR_FEEDBACK
from .constants import CHAR_TX
from .constants import PyHatchBabyRestSound


class SaveConnectBleakClient(BleakClient):
    was_open: bool = False
    async def __aenter__(self):
        if self.is_connected:
            self.was_open = True
        else:
            await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if not self.was_open:
            await self.disconnect()
            self.was_open = False


class PyHatchBabyRestAsync(object):
    """An asynchronous interface to a Hatch Baby Rest device using bleak."""

    def __init__(
        self,
        address_or_ble_device: Union[str, BLEDevice, None] = None,
        scanner: Optional[BleakScanner] = None,
        scan_now: bool = True,
        refresh_now: bool = True,
        auto_refresh: bool = True,
    ):
        self.scanner = scanner
        self.auto_refresh = auto_refresh

        self.device: Optional[BLEDevice]
        self.address: Optional[str]
        self._client: Optional[SaveConnectBleakClient] = None

        if isinstance(address_or_ble_device, BLEDevice):
            self.device = address_or_ble_device
            self.address = address_or_ble_device.address
        else:
            self.address = address_or_ble_device
            if scan_now:
                self.device = asyncio.get_event_loop().run_until_complete(self.scan())
            else:
                self.device = None

        if refresh_now:
            asyncio.get_event_loop().run_until_complete(self.refresh_data())

    @property
    async def client(self) -> SaveConnectBleakClient:
        if self._client:
            return self._client

        self._client = SaveConnectBleakClient(await self._ensure_scan())

        return self._client

    async def _ensure_scan(self) -> BLEDevice:
        """Ensures that a device has been scanned for in case it was skipped on init"""
        if not self.device:
            return await self.scan()
        return self.device

    async def _send_command(self, command: str):
        """Send a command do the device.

        :param command: The command to send.
        """
        self.device = await self._ensure_scan()

        async with await self.client as client:
            await client.write_gatt_char(
                char_specifier=CHAR_TX,
                data=bytearray(command, "utf-8"),
                response=True,
            )

            if self.auto_refresh:
                await asyncio.sleep(0.25)
                await self.refresh_data()

    async def scan(self) -> BLEDevice:
        self.scanner = BleakScanner() if self.scanner is None else self.scanner

        if self.address:
            device = await self.scanner.find_device_by_address(self.address)
        else:
            device = await self.scanner.find_device_by_filter(
                lambda device, _: BT_MANUFACTURER_ID
                in device.metadata["manufacturer_data"].keys()
            )

        if device is None:
            raise RuntimeError(
                "No address or BLEDevice provided and cannot find device in scan"
            )

        self.device = device
        self.address = device.address

        return self.device

    async def refresh_data(self):
        self.device = await self._ensure_scan()

        async with await self.client as client:
            raw_char_read = await client.read_gatt_char(CHAR_FEEDBACK)

        response = [hex(x) for x in raw_char_read]

        timestamp = int.from_bytes(bytes(raw_char_read[1:5]))

        # Make sure the data is where we think it is
        assert response[5] == "0x43"  # color
        assert response[10] == "0x53"  # audio
        assert response[13] == "0x50"  # power

        red, green, blue, brightness = [int(x, 16) for x in response[6:10]]

        sound = PyHatchBabyRestSound(int(response[11], 16))

        volume = int(response[12], 16)

        power = not bool(int("11000000", 2) & int(response[14], 16))

        self.time = datetime.fromtimestamp(timestamp, UTC)
        self.color = (red, green, blue)
        self.brightness = brightness
        self.sound = sound
        self.volume = volume
        self.power = power

    async def connect(self):
        client = await self.client
        if not client.is_connected:
            return await client.connect()

    async def disconnect(self):
        client = await self.client
        if client.is_connected:
            return await client.disconnect()

    async def power_on(self):
        command = "SI{:02x}".format(1)
        self.power = True
        await self._send_command(command)

    async def power_off(self):
        command = "SI{:02x}".format(0)
        self.power = False
        await self._send_command(command)

    async def set_sound(self, sound: PyHatchBabyRestSound):
        command = "SN{:02x}".format(sound)
        self.sound = sound
        return await self._send_command(command)

    async def set_volume(self, volume: int):
        command = "SV{:02x}".format(volume)
        self.volume = volume
        return await self._send_command(command)

    async def set_color(self, red: int, green: int, blue: int):
        # Always refresh to get latest brightness
        await self.refresh_data()

        command = "SC{:02x}{:02x}{:02x}{:02x}".format(red, green, blue, self.brightness)
        self.color = (red, green, blue)
        return await self._send_command(command)

    async def set_brightness(self, brightness):
        # Always refresh to get latest color
        await self.refresh_data()

        command = "SC{:02x}{:02x}{:02x}{:02x}".format(
            self.color[0], self.color[1], self.color[2], brightness
        )
        self.brightness = brightness
        return await self._send_command(command)

    async def set_time(self, new_time: Optional[datetime] = None):
        if not new_time:
            new_time = datetime.now()

        self.time = new_time

        command = datetime.now().strftime("ST%Y%m%d%H%M%SU")
        return await self._send_command(command)

    @property
    def name(self):
        return self.device.name if self.device else None

    @property
    async def is_connected(self) -> bool:
        if not self._client:
            return False

        client = await self.client
        return client.is_connected


async def connect(
    address_or_ble_device: Union[str, BLEDevice, None] = None,
    scanner: Optional[BleakScanner] = None,
    scan_now: bool = True,
    refresh_now: bool = True,
    auto_refresh: bool = True,
) -> PyHatchBabyRestAsync:
    rest = PyHatchBabyRestAsync(
        address_or_ble_device,
        scanner=scanner,
        scan_now=False,
        refresh_now=False,
        auto_refresh=auto_refresh,
    )

    if scan_now:
        await rest.scan()
    if refresh_now:
        await rest.refresh_data()

    return rest
