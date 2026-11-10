import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import server  # noqa: E402


class R:
    def __init__(self, h):
        self.headers = h


def test_note_from_header():
    assert server._masking_note(R({"x-citec-masking": "net=1,pii=3"})) == "마스킹 적용: net 1, pii 3 (토큰 [PHONE-1] 등은 마스킹된 값이며 복원하지 말 것)\n"


def test_no_note_without_header():
    assert server._masking_note(R({})) == ""
