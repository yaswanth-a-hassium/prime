"""The microphone registry: stable names, remembered after unplugging, with what was learned about each."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mics  # noqa: E402


class FakeSound:
    """sounddevice with three audio interfaces, as Windows reports them."""
    def __init__(self, devices, default_name):
        self.devices, self.default_name = devices, default_name

    def query_hostapis(self):
        return [{"name": "MME"}, {"name": "Windows DirectSound"}, {"name": "Windows WASAPI"}]

    def query_devices(self, kind=None):
        if kind == "input":
            return {"name": self.default_name}
        return self.devices


def dev(name, api, ins=1):
    return {"name": name, "hostapi": api, "max_input_channels": ins}


LAPTOP_FULL = "Microphone Array (Intel® Smart Sound Technology for Digital Microphones)"
USB_FULL = "Microphone (USB PnP Audio Device)"
DEVICES = [dev(LAPTOP_FULL[:31], 0), dev(USB_FULL[:31], 0), dev("Speakers (Realtek)", 0, ins=0),
           dev(LAPTOP_FULL[:31], 1), dev(LAPTOP_FULL, 2), dev(USB_FULL, 2), dev("Speakers (Realtek)", 2, ins=0)]


@pytest.fixture(autouse=True)
def windows_interfaces(monkeypatch):
    """These cases replay how Windows names microphones; on the Mac the code asks Core Audio instead."""
    monkeypatch.setattr(mics, "NAMES_API", "WASAPI")
    monkeypatch.setattr(mics, "OPEN_API", "MME")


@pytest.fixture
def usb_default(monkeypatch):
    monkeypatch.setattr(mics, "sd", FakeSound(DEVICES, USB_FULL[:31]))


class MacSound:
    """sounddevice on a MacBook Pro: one interface, Core Audio, full names."""
    def query_hostapis(self):
        return [{"name": "Core Audio"}]

    def query_devices(self, kind=None):
        devices = [dev("MacBook Pro Microphone", 0), dev("MacBook Pro Speakers", 0, ins=0), dev("AirPods Pro", 0)]
        return {"name": "MacBook Pro Microphone"} if kind == "input" else devices


def test_core_audio_on_the_mac(monkeypatch):
    monkeypatch.setattr(mics, "sd", MacSound())
    monkeypatch.setattr(mics, "NAMES_API", "Core Audio")
    monkeypatch.setattr(mics, "OPEN_API", "Core Audio")
    assert mics.default_key() == "MacBook Pro Microphone"
    assert [m["key"] for m in mics.connected()] == ["MacBook Pro Microphone", "AirPods Pro"]
    assert mics.stream_index("AirPods Pro") == 2


def test_the_short_name_windows_reports_becomes_the_full_stable_name(usb_default):
    assert mics.key_for(USB_FULL[:31]) == USB_FULL
    assert mics.key_for(USB_FULL) == USB_FULL
    assert mics.default_key() == USB_FULL


def test_an_unknown_name_stays_as_it_is(usb_default):
    assert mics.key_for("Some Headset") == "Some Headset" and mics.key_for("") == ""


def test_connected_lists_each_mic_once_and_marks_the_default(usb_default):
    got = mics.connected()
    assert [m["key"] for m in got] == [LAPTOP_FULL, USB_FULL]
    assert [m["default"] for m in got] == [False, True]


def test_a_mic_is_opened_through_the_interface_that_allows_16khz(usb_default):
    assert mics.stream_index(USB_FULL) == 1 and mics.stream_index(LAPTOP_FULL) == 0
    assert mics.stream_index("nothing here") is None and mics.stream_index("") is None


def test_no_microphone_at_all_is_not_an_error(monkeypatch):
    class Broken(FakeSound):
        def query_devices(self, kind=None):
            raise OSError("no input device")
    monkeypatch.setattr(mics, "sd", Broken([], ""))
    assert mics.default_key() == ""


def test_a_new_mic_is_noticed_once_and_remembered_after_unplugging(tmp_path):
    reg = mics.Registry(tmp_path / "m.json")
    assert reg.seen(USB_FULL) is True and reg.seen(USB_FULL) is False
    reg.update(USB_FULL, match=0.37, clap_peak=1800, imprinted=True)
    again = mics.Registry(tmp_path / "m.json")                  # a restart, mic unplugged meanwhile
    assert again.match_for(USB_FULL) == 0.37 and again.clap_for(USB_FULL) == 1800 and again.imprinted(USB_FULL)
    assert again.match_for("never seen") is None and not again.imprinted("never seen")


def test_forgetting_a_mic(tmp_path):
    reg = mics.Registry(tmp_path / "m.json")
    reg.seen(USB_FULL)
    assert reg.forget(USB_FULL) and not reg.forget(USB_FULL) and reg.known() == {}


def test_a_damaged_file_starts_empty(tmp_path):
    (tmp_path / "m.json").write_text("{oops", encoding="utf-8")
    assert mics.Registry(tmp_path / "m.json").known() == {}
