"""Browser speech frames. Car browsers send base64 PCM instead of binary."""

import base64
import json

from remote_dev_ctrl.server.app import parse_stt_client_text


def test_close_messages_stop_the_stream():
    assert parse_stt_client_text('{"type":"CloseStream"}') == ("stop", None)
    assert parse_stt_client_text('{"type":"stop"}') == ("stop", None)


def test_audio_frame_decodes_to_pcm():
    raw = b"\x01\x02\xff\x00"
    payload = base64.b64encode(raw).decode()
    kind, audio = parse_stt_client_text(json.dumps({"type": "audio", "audio": payload}))
    assert kind == "audio"
    assert audio == raw


def test_junk_and_oversized_frames_are_ignored():
    assert parse_stt_client_text("nope") == ("ignore", None)
    assert parse_stt_client_text("[]") == ("ignore", None)
    assert parse_stt_client_text('{"type":"audio","audio":"!!!!"}') == ("ignore", None)
    assert parse_stt_client_text(json.dumps({"type": "audio", "audio": "A" * 100_001})) == ("ignore", None)
