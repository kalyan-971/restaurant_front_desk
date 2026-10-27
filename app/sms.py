"""Sending texts. TwilioSender is real; FakeSender records messages for tests."""


class TwilioSender:
    def __init__(self, sid: str, token: str):
        from twilio.rest import Client
        self.client = Client(sid, token)

    def send(self, from_: str, to: str, body: str) -> None:
        self.client.messages.create(from_=from_, to=to, body=body)


class FakeSender:
    def __init__(self):
        self.sent: list[dict] = []

    def send(self, from_: str, to: str, body: str) -> None:
        self.sent.append({"from": from_, "to": to, "body": body})
