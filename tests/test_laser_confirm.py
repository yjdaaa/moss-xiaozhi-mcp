import unittest

from core.laser_confirm import is_explicitly_confirmed


class LaserConfirmTests(unittest.TestCase):
    def test_false_variants(self):
        for value in (False, None, "", "false", "FALSE", "0", "off", "no", "n", "否", "取消"):
            with self.subTest(value=value):
                self.assertFalse(is_explicitly_confirmed(value))

    def test_true_variants(self):
        for value in (True, 1, "1", "true", "TRUE", "yes", "y", "on", "confirmed", "确认"):
            with self.subTest(value=value):
                self.assertTrue(is_explicitly_confirmed(value))

    def test_python_bool_string_false_trap(self):
        # Document the bug class this helper exists to prevent.
        self.assertTrue(bool("false"))
        self.assertFalse(is_explicitly_confirmed("false"))


if __name__ == "__main__":
    unittest.main()
