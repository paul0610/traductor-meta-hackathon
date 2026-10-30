"""One clock for the whole process (SPEC §7.2).

Every `t_*` field is `time.perf_counter()`; never `time.time()`. The ASR speaks
in stream-relative time (`audioProcessedMs`); `StreamClock` maps it onto the
local clock from the capture time of the first block sent in the session.
"""

import time

now = time.perf_counter


def block_start(n_frames: int, sample_rate: int, t_callback: float | None = None) -> float:
    """`t_capture` of a block: the callback instant minus the block duration (its first sample)."""
    t = now() if t_callback is None else t_callback
    return t - n_frames / sample_rate


class StreamClock:
    """Maps one ASR session's stream time onto `perf_counter`.

    `t_capture_first` is the `t_capture` of the first block sent in the session,
    not the instant it was sent. A reconnect starts a new session and a new origin.
    """

    __slots__ = ("session_id", "t_capture_first")

    def __init__(self) -> None:
        self.session_id: str | None = None
        self.t_capture_first: float | None = None

    def start_session(self, session_id: str) -> None:
        self.session_id = session_id
        self.t_capture_first = None

    def mark_first_block(self, t_capture: float) -> None:
        if self.t_capture_first is None:
            self.t_capture_first = t_capture

    def to_local(self, t_asr_s: float | None) -> float | None:
        if t_asr_s is None or self.t_capture_first is None:
            return None
        return self.t_capture_first + t_asr_s

    def lag(self, t_recv: float, t_asr_s: float | None) -> float | None:
        """Transport + buffering lag of an event: receipt minus the stream position it reports."""
        t_local = self.to_local(t_asr_s)
        return None if t_local is None else t_recv - t_local
