from __future__ import annotations

from nps_lab_el.crypto.aes_gcm import compute_key_fragment_hmac
from nps_lab_el.crypto.totp import get_counter
from nps_lab_el.models.auth import KnockSession
from nps_lab_el.models.config import AppConfig

# Marks lab DRM ICMP replies (Windows auto-replies use code 0).
DRM_ICMP_CODE = 0x5A

# Full 32-byte key delivery: 8 ICMP replies × 4 bytes each.
_KEY_BYTES = 32
_BYTES_PER_REPLY = 4
FULL_KEY_REPLY_PACKETS = _KEY_BYTES // _BYTES_PER_REPLY  # 8


class DrmResponder:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def compute_fragment(self, session: KnockSession) -> bytes:
        counter = get_counter(self.config.auth.window_seconds)
        return compute_key_fragment_hmac(
            key_deriv=self.config.auth.totp_secret.encode(),
            totp_counter=counter,
            src_ip=session.src_ip,
        )

    def encode_reply_fields(self, fragment: bytes, session_nonce: int) -> dict:
        return {
            "ttl": fragment[0],
            "tos": fragment[1],
            "icmp_id": int.from_bytes(fragment[2:4], "big") ^ session_nonce,
        }

    def build_reply_packet(self, src_ip: str, dst_ip: str, fragment: bytes, session_nonce: int):
        from scapy.all import IP, ICMP

        fields = self.encode_reply_fields(fragment, session_nonce)
        return (
            IP(src=src_ip, dst=dst_ip, ttl=fields["ttl"], tos=fields["tos"])
            / ICMP(type=0, code=DRM_ICMP_CODE, id=fields["icmp_id"], seq=0)
        )

    def build_reply_packets_full_key(self, src_ip: str, dst_ip: str, key: bytes, session_nonce: int) -> list:
        """Build 8 ICMP echo-reply packets that together encode a full 32-byte segment key.
        Each packet carries 4 bytes in TTL+TOS+ICMP_ID; seq number (0-7) identifies position."""
        from scapy.all import IP, ICMP

        assert len(key) == _KEY_BYTES, f"key must be {_KEY_BYTES} bytes"
        packets = []
        for seq_idx in range(FULL_KEY_REPLY_PACKETS):
            chunk = key[seq_idx * _BYTES_PER_REPLY: (seq_idx + 1) * _BYTES_PER_REPLY]
            fields = self.encode_reply_fields(chunk, session_nonce)
            packets.append(
                IP(src=src_ip, dst=dst_ip, ttl=fields["ttl"], tos=fields["tos"])
                / ICMP(type=0, code=DRM_ICMP_CODE, id=fields["icmp_id"], seq=seq_idx)
            )
        return packets

    @staticmethod
    def extract_key_fragment(reply_ttl: int, reply_tos: int, reply_icmp_id: int, session_nonce: int) -> bytes:
        val = (reply_ttl << 24) | (reply_tos << 16) | ((reply_icmp_id ^ session_nonce) & 0xFFFF)
        return val.to_bytes(4, "big")

    @staticmethod
    def extract_full_key_from_replies(replies: list, session_nonce: int) -> bytes | None:
        """Reassemble a 32-byte key from 8 ICMP echo-reply packets (seq 0-7).
        Returns None if fewer than 8 valid DRM replies are present."""
        from scapy.all import IP, ICMP

        chunks: dict[int, bytes] = {}
        for pkt in replies:
            if not pkt.haslayer(ICMP) or pkt[ICMP].type != 0:
                continue
            if pkt[ICMP].code != DRM_ICMP_CODE:
                continue
            seq = pkt[ICMP].seq
            if seq >= FULL_KEY_REPLY_PACKETS:
                continue
            b0 = pkt[IP].ttl & 0xFF
            b1 = pkt[IP].tos & 0xFF
            val = (pkt[ICMP].id ^ (session_nonce & 0xFFFF)) & 0xFFFF
            chunks[seq] = bytes([b0, b1, (val >> 8) & 0xFF, val & 0xFF])

        if len(chunks) < FULL_KEY_REPLY_PACKETS:
            return None
        return b"".join(chunks[i] for i in range(FULL_KEY_REPLY_PACKETS))
