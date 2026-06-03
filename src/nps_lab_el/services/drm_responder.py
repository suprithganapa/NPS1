from __future__ import annotations

from nps_lab_el.crypto.aes_gcm import compute_key_fragment_hmac
from nps_lab_el.crypto.totp import get_counter
from nps_lab_el.models.auth import KnockSession
from nps_lab_el.models.config import AppConfig

# Marks lab DRM ICMP replies (Windows auto-replies use code 0).
DRM_ICMP_CODE = 0x5A


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

    @staticmethod
    def extract_key_fragment(reply_ttl: int, reply_tos: int, reply_icmp_id: int, session_nonce: int) -> bytes:
        val = (reply_ttl << 24) | (reply_tos << 16) | ((reply_icmp_id ^ session_nonce) & 0xFFFF)
        return val.to_bytes(4, "big")
