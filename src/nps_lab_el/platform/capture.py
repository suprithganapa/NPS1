import threading
import time
from collections.abc import Callable

from nps_lab_el.models.auth import CaptureEvent


class CaptureBackend:
    def __init__(
        self,
        iface: str,
        server_ip: str,
        simulation: bool = False,
        *,
        same_host: bool = False,
    ) -> None:
        self.iface = iface
        self.server_ip = server_ip
        self.simulation = simulation
        self.same_host = same_host

    def start_capture(
        self,
        callback: Callable,
        timeout: float | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        if self.simulation:
            return
        from scapy.all import sniff

        stop_filter = None
        if stop_event is not None:
            stop_filter = lambda _pkt: stop_event.is_set()

        sniff(
            iface=self.iface,
            filter="icmp",
            prn=callback,
            store=0,
            timeout=timeout,
            stop_filter=stop_filter,
        )

    def parse_packet(self, packet) -> CaptureEvent | None:
        from scapy.all import ICMP, IP

        if not packet.haslayer(IP) or not packet.haslayer(ICMP):
            return None

        ip_layer = packet[IP]
        icmp_layer = packet[ICMP]

        dst_ok = ip_layer.dst == self.server_ip
        if self.same_host and icmp_layer.type == 8 and ip_layer.dst == "127.0.0.1":
            dst_ok = True

        if icmp_layer.type == 8 and dst_ok:
            pass
        elif icmp_layer.type == 0 and ip_layer.src == self.server_ip:
            pass
        else:
            return None

        pkt_ts = float(getattr(packet, "time", time.time()))
        return CaptureEvent(
            ts=pkt_ts,
            src=ip_layer.src,
            dst=ip_layer.dst,
            icmp_id=icmp_layer.id,
            icmp_seq=icmp_layer.seq,
            ip_id=ip_layer.id,
            ttl=ip_layer.ttl,
            tos=ip_layer.tos,
            icmp_type=icmp_layer.type,
        )


class SimulationCapture(CaptureBackend):
    def __init__(self, events: list[CaptureEvent]) -> None:
        self.events = events
        self.iface = ""
        self.server_ip = ""
        self.simulation = True

    def start_capture(
        self,
        callback: Callable,
        timeout: float | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        for event in self.events:
            if stop_event is not None and stop_event.is_set():
                break
            callback(event)

    def parse_packet(self, packet) -> CaptureEvent | None:
        if isinstance(packet, CaptureEvent):
            return packet
        return None
