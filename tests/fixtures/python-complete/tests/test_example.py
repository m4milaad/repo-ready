import unittest

from example_app import message


class ExampleTest(unittest.TestCase):
    def test_message(self) -> None:
        self.assertEqual(message(), "complete python fixture")


if __name__ == "__main__":
    unittest.main()
