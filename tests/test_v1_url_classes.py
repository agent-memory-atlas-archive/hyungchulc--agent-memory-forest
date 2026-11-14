"""The linear URL scan has the old pattern's Unicode meaning. All texts are invented."""

import random
import re
import unittest

import bounds
from memory_forest import numbers


OLD_URL = re.compile(r"[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)


class UnicodeTests(unittest.TestCase):
    def test_every_unicode_character_at_every_class(self):
        first, continuing, excluded = numbers.url_character_classes()
        a, b, c = (re.compile(part, re.IGNORECASE) for part in (r"[a-z]", r"[a-z0-9+.-]", r"\S"))
        for point in range(0x110000):
            char = chr(point)
            self.assertEqual((char in first, char in continuing, char not in excluded),
                             (bool(a.fullmatch(char)), bool(b.fullmatch(char)), bool(c.fullmatch(char))), hex(point))
            for text in (char + "://x", "a" + char + "://x", "a://" + char):
                self.assertEqual(numbers.url_spans(text), [m.span() for m in OLD_URL.finditer(text)], hex(point))

    def test_generated_texts_have_exactly_the_old_spans(self):
        rng = random.Random(4107)
        alphabet = "aZ01+.-:/ \t\nİıſKéΩ_" + "\x85\u2028"
        texts = [char + "://account=123456789012" for char in "İıſK"]
        texts += ["".join(rng.choices(alphabet, k=rng.randrange(90))) for _ in range(8000)]
        texts += ["".join(rng.choice(("a://x", "İ://x", "://", "x", " ", "+.-"))
                          for _ in range(20)) for _ in range(1000)]
        for text in texts:
            self.assertEqual(numbers.url_spans(text), [m.span() for m in OLD_URL.finditer(text)], repr(text))


if __name__ == "__main__":
    unittest.main()
