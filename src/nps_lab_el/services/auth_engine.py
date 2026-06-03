from __future__ import annotations

import hashlib
import hmac
import time

from nps_lab_el.crypto.totp import check_replay, get_counter, generate_totp
from nps_lab_el.models.auth import (
    AuthorizationState,
    AuthToken,
    CaptureEvent,
    KnockSession,
)
from nps_lab_el.models.config import AppConfig
from nps_lab_el.protocol.decode import decode_jitter_burst, sessionize_events
from nps_lab_el.protocol.encode_header import decode_header_sequence


class AuthEngine:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.replay_cache: dict[str, int] = {}
        self.active_sessions: dict[str, KnockSession] = {}

    def process_events(self, events: list[CaptureEvent]) -> KnockSession:
        if not events:
            return KnockSession(src_ip="unknown", state=AuthorizationState.DENIED)
        sessions = sessionize_events(events)
        session: KnockSession | None = None
        for group in sessions:
            token = self._try_jitter_decode(group)
            if token is None:
                token = self._try_header_decode(group)
            src_ip = group[0].src
            if token is not None and self._verify_token(token, src_ip):
                session = self._create_session(src_ip, group, AuthorizationState.AUTHORIZED)
            else:
                session = self._create_session(src_ip, group, AuthorizationState.DENIED)
            self.active_sessions[session.session_id] = session
        if session is None:
            return KnockSession(src_ip=events[0].src, state=AuthorizationState.DENIED)
        return session

    def _try_jitter_decode(self, events: list[CaptureEvent]) -> AuthToken | None:
        jc = self.config.channels.jitter
        if not jc.enabled:
            return None
        bits = decode_jitter_burst(
            events,
            t0_ms=jc.t0_ms,
            delta_ms=jc.delta_ms,
            tau_ms=jc.tau_ms,
            fec_n=self.config.fec.n,
            fec_k=self.config.fec.k,
            expected_bits=AuthToken.total_bits(),
        )
        if bits is None:
            return None
        return AuthToken.from_bits(bits)

    def _try_header_decode(self, events: list[CaptureEvent]) -> AuthToken | None:
        hc = self.config.channels.header
        if not hc.enabled:
            return None
        ip_ids = [e.ip_id for e in events]
        icmp_seqs = [e.icmp_seq for e in events]
        bits = decode_header_sequence(ip_ids, icmp_seqs, lfsr_seed=hc.lfsr_seed)
        if bits is None:
            return None
        return AuthToken.from_bits(bits)

    def _verify_token(self, token: AuthToken, src_ip: str) -> bool:
        secret = self.config.auth.totp_secret
        window = self.config.auth.window_seconds

        expected_counter = get_counter(window)
        _, expected_totp_counter = generate_totp(secret, window)
        if abs(token.totp_counter - expected_counter) > 1:
            return False

        mac_input = token.payload + token.totp_counter.to_bytes(8, "big") + token.channel_mask.to_bytes(1, "big")
        expected_mac = hmac.new(secret.encode(), mac_input, hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(token.mac, expected_mac):
            return False

        if check_replay(src_ip, token.totp_counter, self.replay_cache, window):
            return False

        return True

    def _create_session(
        self, src_ip: str, events: list[CaptureEvent], state: AuthorizationState
    ) -> KnockSession:
        iats = [events[i + 1].ts - events[i].ts for i in range(len(events) - 1)] if len(events) > 1 else []
        return KnockSession(
            src_ip=src_ip,
            started_at=events[0].ts,
            symbols=[e.icmp_seq for e in events],
            iat_observations=iats,
            state=state,
            timeout_seconds=float(self.config.auth.ttl_auth_seconds),
        )

    def get_session(self, session_id: str) -> KnockSession | None:
        return self.active_sessions.get(session_id)

    def cleanup_expired(self) -> None:
        now = time.time()
        expired = [
            sid
            for sid, s in self.active_sessions.items()
            if s.started_at + s.timeout_seconds < now
        ]
        for sid in expired:
            del self.active_sessions[sid]
