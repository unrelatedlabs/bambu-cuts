"""
Raw MQTT report dumping for Bambu Lab printers.

This talks directly to the printer's LAN MQTT broker instead of going through
the bambulabs_api wrapper, which makes it useful for discovering fields that
the wrapper does not expose yet.
"""

from __future__ import annotations

import json
import queue
import ssl
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterator, List, Optional

import paho.mqtt.client as mqtt


BAMBU_MQTT_PORT = 8883
BAMBU_MQTT_USERNAME = "bblp"
BAMBU_REPORT_TOPIC = "device/{serial}/report"
BAMBU_REQUEST_TOPIC = "device/{serial}/request"


class MqttDumpError(RuntimeError):
    """Raised when the MQTT dumper cannot connect or collect data."""


MessageCallback = Callable[[Dict[str, Any]], None]


@dataclass
class MqttMessage:
    """One raw MQTT message plus a parsed JSON form when possible."""

    timestamp: float
    topic: str
    qos: int
    retain: bool
    payload: str
    json_payload: Optional[Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "topic": self.topic,
            "qos": self.qos,
            "retain": self.retain,
            "payload": self.payload,
            "json": self.json_payload,
        }


def _new_client(client_id: str) -> mqtt.Client:
    """Create a paho client compatible with both paho-mqtt 1.x and 2.x."""
    try:
        return mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
            client_id=client_id,
            protocol=mqtt.MQTTv311,
        )
    except (AttributeError, TypeError):
        return mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311)


def _decode_payload(payload: bytes) -> tuple[str, Optional[Any]]:
    text = payload.decode("utf-8", errors="replace")
    try:
        return text, json.loads(text)
    except json.JSONDecodeError:
        return text, None


def _wait_first(timeout: float, *events: threading.Event) -> Optional[threading.Event]:
    """Wait until one of the events is set. Returns that event, or None on timeout."""
    deadline = time.monotonic() + timeout
    while True:
        for event in events:
            if event.is_set():
                return event
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        time.sleep(min(0.05, remaining))


def _pushall_payload(sequence_id: str) -> str:
    return json.dumps({
        "pushing": {
            "sequence_id": sequence_id,
            "command": "pushall",
        }
    })


def dump_mqtt(
    ip: str,
    access_code: str,
    serial: str,
    *,
    duration: float = 5.0,
    max_messages: int = 5,
    request_pushall: bool = True,
    port: int = BAMBU_MQTT_PORT,
    connect_timeout: float = 10.0,
    on_message: Optional[MessageCallback] = None,
) -> Dict[str, Any]:
    """
    Collect raw MQTT report messages from a Bambu printer.

    Args:
        ip: Printer IP address.
        access_code: LAN access code, used as the MQTT password.
        serial: Printer serial number, used in MQTT topics.
        duration: Seconds to collect after connecting. Use None to wait until
            max_messages is reached or the caller interrupts the process.
        max_messages: Stop after this many messages. Use 0 for no count limit.
        request_pushall: Publish a pushall request to trigger a full report.
        port: Printer MQTT TLS port.
        connect_timeout: Seconds to wait for MQTT connection.
        on_message: Optional callback called immediately for each message.

    Returns:
        A JSON-serializable dict containing raw payloads and parsed JSON.
    """
    ip = (ip or "").strip()
    access_code = (access_code or "").strip()
    serial = (serial or "").strip()

    if not ip:
        raise MqttDumpError("Missing printer IP")
    if not access_code:
        raise MqttDumpError("Missing printer access code")
    if not serial:
        raise MqttDumpError("Missing printer serial")

    if duration is None:
        wait_timeout = None
    else:
        duration = max(0.1, float(duration))
        wait_timeout = duration

    max_messages = max(0, int(max_messages))
    port = int(port)

    report_topic = BAMBU_REPORT_TOPIC.format(serial=serial)
    request_topic = BAMBU_REQUEST_TOPIC.format(serial=serial)
    sequence_id = str(int(time.time() * 1000))
    client_id = f"bambucuts-dump-{uuid.uuid4().hex[:8]}"

    messages: List[MqttMessage] = []
    errors: List[str] = []
    connected = threading.Event()
    complete = threading.Event()
    on_message_callback = on_message

    client = _new_client(client_id)
    client.username_pw_set(BAMBU_MQTT_USERNAME, access_code)
    client.tls_set(cert_reqs=ssl.CERT_NONE)
    client.tls_insecure_set(True)

    def on_connect(client, userdata, flags, rc):
        if rc != 0:
            errors.append(f"MQTT connect failed with rc={rc}")
            complete.set()
            return

        connected.set()
        client.subscribe(report_topic)
        if request_pushall:
            client.publish(request_topic, _pushall_payload(sequence_id))

    def handle_paho_message(client, userdata, message):
        text, parsed = _decode_payload(message.payload)
        mqtt_message = MqttMessage(
            timestamp=time.time(),
            topic=message.topic,
            qos=message.qos,
            retain=message.retain,
            payload=text,
            json_payload=parsed,
        )
        messages.append(mqtt_message)

        if on_message_callback:
            try:
                on_message_callback(mqtt_message.to_dict())
            except Exception as exc:
                errors.append(f"Message callback failed: {exc}")

        if max_messages and len(messages) >= max_messages:
            complete.set()

    def on_disconnect(client, userdata, rc):
        if rc != 0 and not complete.is_set():
            errors.append(f"MQTT disconnected with rc={rc}")
            complete.set()

    client.on_connect = on_connect
    client.on_message = handle_paho_message
    client.on_disconnect = on_disconnect

    started_at = time.time()

    try:
        client.connect(ip, port, keepalive=60)
        client.loop_start()

        if not connected.wait(connect_timeout):
            if errors:
                raise MqttDumpError(errors[-1])
            raise MqttDumpError(f"Timed out connecting to MQTT at {ip}:{port}")

        complete.wait(wait_timeout)
    except OSError as exc:
        raise MqttDumpError(f"MQTT connection error: {exc}") from exc
    finally:
        client.loop_stop()
        client.disconnect()

    elapsed = time.time() - started_at
    return {
        "success": True,
        "ip": ip,
        "port": port,
        "report_topic": report_topic,
        "request_topic": request_topic,
        "requested_pushall": request_pushall,
        "sequence_id": sequence_id if request_pushall else None,
        "duration": duration,
        "elapsed": elapsed,
        "message_count": len(messages),
        "messages": [message.to_dict() for message in messages],
        "errors": errors,
    }


def stream_mqtt(
    ip: str,
    access_code: str,
    serial: str,
    *,
    duration: Optional[float] = 60.0,
    max_messages: int = 0,
    request_pushall: bool = True,
    port: int = BAMBU_MQTT_PORT,
    connect_timeout: float = 10.0,
) -> Iterator[Dict[str, Any]]:
    """Yield MQTT messages as NDJSON-friendly event dictionaries."""
    events: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue()

    def handle_message(message: Dict[str, Any]) -> None:
        events.put({
            "type": "message",
            "message": message,
        })

    def worker() -> None:
        try:
            result = dump_mqtt(
                ip,
                access_code,
                serial,
                duration=duration,
                max_messages=max_messages,
                request_pushall=request_pushall,
                port=port,
                connect_timeout=connect_timeout,
                on_message=handle_message,
            )
            events.put({
                "type": "summary",
                "message_count": result["message_count"],
                "elapsed": result["elapsed"],
                "errors": result["errors"],
            })
        except MqttDumpError as exc:
            events.put({
                "type": "error",
                "error": str(exc),
            })
        finally:
            events.put(None)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    while True:
        event = events.get()
        if event is None:
            break
        yield event


def listen_mqtt_reports(
    ip: str,
    access_code: str,
    serial: str,
    on_message: MessageCallback,
    *,
    stop_event: Optional[threading.Event] = None,
    request_pushall: bool = True,
    port: int = BAMBU_MQTT_PORT,
    connect_timeout: float = 10.0,
    pushall_interval: Optional[Callable[[], Optional[float]]] = None,
    stats: Optional[Dict[str, Any]] = None,
) -> None:
    """Listen to MQTT reports until stop_event is set.

    stop_event belongs to the caller and is only ever read here: a failed or
    dropped connection ends this session by raising MqttDumpError, so a caller
    looping on stop_event can reconnect instead of being shut down for good.

    pushall_interval, when given, is polled continuously; while it returns a
    positive number of seconds, a pushall request is published at that
    interval so the printer reports its full state on demand instead of on
    its own cadence. Returning None or 0 pauses the requests.

    stats, when given, is updated in place with 'pushall_count' and
    'last_pushall_at' (monotonic). Each message handed to on_message carries a
    'meta' dict: receive time, whether its sequence_id matched a pushall we
    sent and that pushall's round trip, time since the last pushall, and the
    running pushall count. This is what lets a caller tell a solicited full
    report from the printer's own unsolicited delta.
    """
    ip = (ip or "").strip()
    access_code = (access_code or "").strip()
    serial = (serial or "").strip()

    if not ip:
        raise MqttDumpError("Missing printer IP")
    if not access_code:
        raise MqttDumpError("Missing printer access code")
    if not serial:
        raise MqttDumpError("Missing printer serial")

    stop_event = stop_event or threading.Event()
    report_topic = BAMBU_REPORT_TOPIC.format(serial=serial)
    request_topic = BAMBU_REQUEST_TOPIC.format(serial=serial)
    sequence_id = str(int(time.time() * 1000))
    client_id = f"bambucuts-status-{uuid.uuid4().hex[:8]}"
    errors: List[str] = []
    connected = threading.Event()
    # Set when this connection dies. Session-scoped on purpose: setting the
    # caller's stop_event here would end their listen loop permanently.
    session_over = threading.Event()

    client = _new_client(client_id)
    client.username_pw_set(BAMBU_MQTT_USERNAME, access_code)
    client.tls_set(cert_reqs=ssl.CERT_NONE)
    client.tls_insecure_set(True)

    stats = stats if stats is not None else {}
    stats.setdefault('pushall_count', 0)
    stats.setdefault('last_pushall_at', None)
    pending_pushalls: Dict[str, float] = {}
    pushall_lock = threading.Lock()

    def send_pushall(seq: str) -> None:
        now = time.monotonic()
        with pushall_lock:
            pending_pushalls[seq] = now
            while len(pending_pushalls) > 64:
                pending_pushalls.pop(next(iter(pending_pushalls)))
            stats['pushall_count'] = stats.get('pushall_count', 0) + 1
            stats['last_pushall_at'] = now
        client.publish(request_topic, _pushall_payload(seq))

    def on_connect(client, userdata, flags, rc):
        if rc != 0:
            errors.append(f"MQTT connect failed with rc={rc}")
            session_over.set()
            return

        connected.set()
        client.subscribe(report_topic)
        if request_pushall:
            send_pushall(sequence_id)

    def handle_paho_message(client, userdata, message):
        text, parsed = _decode_payload(message.payload)
        now = time.monotonic()
        seq = None
        if isinstance(parsed, dict) and isinstance(parsed.get('print'), dict):
            seq = parsed['print'].get('sequence_id')
        with pushall_lock:
            sent_at = pending_pushalls.pop(str(seq), None) if seq is not None else None
            last_pushall = stats.get('last_pushall_at')
            count = stats.get('pushall_count', 0)
        meta = {
            'received_mono': now,
            'sequence_id': seq,
            'pushall_matched': sent_at is not None,
            'pushall_rtt': (now - sent_at) if sent_at is not None else None,
            'since_last_pushall': (now - last_pushall) if last_pushall is not None else None,
            'pushall_count': count,
        }
        mqtt_message = MqttMessage(
            timestamp=time.time(),
            topic=message.topic,
            qos=message.qos,
            retain=message.retain,
            payload=text,
            json_payload=parsed,
        )
        record = mqtt_message.to_dict()
        record['meta'] = meta
        on_message(record)

    def on_disconnect(client, userdata, rc):
        if rc != 0 and not stop_event.is_set():
            errors.append(f"MQTT disconnected with rc={rc}")
        session_over.set()

    client.on_connect = on_connect
    client.on_message = handle_paho_message
    client.on_disconnect = on_disconnect

    try:
        client.connect(ip, int(port), keepalive=60)
        client.loop_start()

        outcome = _wait_first(connect_timeout, connected, session_over, stop_event)
        if outcome is stop_event:
            return
        if outcome is not connected:
            if errors:
                raise MqttDumpError(errors[-1])
            raise MqttDumpError(f"Timed out connecting to MQTT at {ip}:{port}")

        next_pushall = 0.0
        while not stop_event.wait(0.05):
            if session_over.is_set():
                raise MqttDumpError(errors[-1] if errors else "MQTT connection lost")
            interval = pushall_interval() if pushall_interval else None
            if interval and interval > 0:
                now = time.monotonic()
                if now >= next_pushall:
                    send_pushall(str(int(time.time() * 1000)))
                    next_pushall = now + interval
    except OSError as exc:
        raise MqttDumpError(f"MQTT connection error: {exc}") from exc
    finally:
        client.loop_stop()
        client.disconnect()


def format_message(message: Dict[str, Any], *, index: Optional[int] = None, raw: bool = False) -> str:
    """Format one MQTT message for terminal output."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(message["timestamp"]))
    prefix = f"message {index}" if index is not None else "message"
    lines = [f"--- {prefix} @ {timestamp} topic={message['topic']} ---"]

    if raw or message["json"] is None:
        lines.append(message["payload"])
    else:
        lines.append(json.dumps(message["json"], indent=2, sort_keys=True))

    return "\n".join(lines)


def format_dump(dump: Dict[str, Any], *, raw: bool = False) -> str:
    """Format a dump result for terminal output."""
    lines = [
        f"MQTT report topic: {dump['report_topic']}",
        f"Messages: {dump['message_count']} in {dump['elapsed']:.2f}s",
    ]

    for index, message in enumerate(dump["messages"], 1):
        lines.append("")
        lines.append(format_message(message, index=index, raw=raw))

    if dump.get("errors"):
        lines.append("")
        lines.append("Errors:")
        lines.extend(f"- {error}" for error in dump["errors"])

    return "\n".join(lines)
