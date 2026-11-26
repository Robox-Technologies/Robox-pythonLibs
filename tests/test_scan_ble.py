import importlib.util
import os
import unittest


_TOOL_PATH = os.path.join(
    os.path.dirname(__file__), "..", "tools", "scan_ble.py"
)
_SPEC = importlib.util.spec_from_file_location("scan_ble", _TOOL_PATH)
scan_ble = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(scan_ble)


class Device:
    def __init__(self, name):
        self.name = name


class Advertisement:
    def __init__(self, local_name):
        self.local_name = local_name


class ScanBleTests(unittest.TestCase):
    def test_prefers_advertised_local_name(self):
        result = scan_ble.advertised_name(
            Device("stale-name"), Advertisement("MyRobot")
        )
        self.assertEqual(result, "MyRobot")

    def test_uses_advertised_local_name_when_device_name_is_missing(self):
        result = scan_ble.advertised_name(
            Device(None), Advertisement("MyRobot")
        )
        self.assertEqual(result, "MyRobot")

    def test_matches_exact_advertised_name(self):
        self.assertTrue(
            scan_ble.matches_name(
                Device(None), Advertisement("MyRobot"), "MyRobot"
            )
        )
        self.assertFalse(
            scan_ble.matches_name(
                Device(None), Advertisement("MyRobot"), "MyRobot-2"
            )
        )


if __name__ == "__main__":
    unittest.main()
