import unittest

from drivux.app import format_size, parse_size


class FormatSizeTest(unittest.TestCase):
    def test_uses_base_1024_like_google(self):
        self.assertEqual(format_size(100 * 1024**3), "100 GB")

    def test_one_decimal_place(self):
        self.assertEqual(format_size(int(5.765 * 1024**3)), "5.8 GB")

    def test_small_sizes(self):
        self.assertEqual(format_size(512), "512 bytes")
        self.assertEqual(format_size(110 * 1024**2), "110 MB")


class ParseSizeTest(unittest.TestCase):
    def test_rclone_suffixes(self):
        self.assertEqual(parse_size("10G"), 10 * 1024**3)
        self.assertEqual(parse_size("512M"), 512 * 1024**2)


if __name__ == "__main__":
    unittest.main()
