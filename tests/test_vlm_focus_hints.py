from app.ai.vlm_focus import shots_focus_x


class FakeGemini:
    def __init__(self):
        self.payload = None

    def generate_content(self, model, payload):
        self.payload = payload
        return {"candidates": [{"content": {"parts": [{"text": '{"points": [{"i": 0, "x": 0.2}, {"i": 1, "x": 0.8}]}'}]}}]}


def test_spoken_line_goes_with_each_shot_frame():
    fake = FakeGemini()
    out = shots_focus_x([b"a", b"b"], client=fake, hints=["Морти, ты что делаешь?", ""])
    assert out == [0.2, 0.8]
    texts = [p["text"] for p in fake.payload["contents"][0]["parts"] if "text" in p]
    assert texts[0] == "Image 0 — spoken in this shot: «Морти, ты что делаешь?»"
    assert texts[1] == "Image 1"
    assert "FACE/HEAD" in texts[-1]  # центр лица, не тела
