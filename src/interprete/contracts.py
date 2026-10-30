"""The three contracts between modules (SPEC §7.3).

No module hands another raw JSON or anonymous tuples. Fields marked ★ feed the
gates and may not be removed.
"""

from dataclasses import dataclass, field
from enum import Enum


class Channel(str, Enum):
    OUT = "out"      # me → them: microphone → CABLE Input
    IN = "in"        # them → me: loopback → overlay


class AsrKind(str, Enum):
    SPEECH_START = "speechStart"
    SPEECH_END = "speechEnd"
    SPEECH_COMPLETE = "speechComplete"
    PARTIAL = "partial"
    FINAL = "final"
    SPEAKER = "speaker"
    PROGRESS = "audioProgress"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class AsrEvent:                        # one WS frame, normalized; raw JSON never leaves asr/
    channel: Channel
    kind: AsrKind
    t_recv: float                      # ★ perf_counter when the frame was received
    session_id: str                    # sessionId from the handshake; changes on reconnect
    turn_id: str | None = None         # turnId (ENDPOINTING / DIARIZATION)
    text: str = ""                     # full CUMULATIVE transcript of the turn (partial, final, speechComplete)
    t_asr_s: float | None = None       # audioProcessedMs / 1000: stream position consumed at emission (SPEC §7.2)
    speaker: str | None = None         # DIARIZATION only
    raw: dict = field(default_factory=dict, compare=False)   # debug log only


@dataclass(slots=True)
class Commit:                          # one committed clause; irreversible from birth
    utterance_id: str                  # ★ f"{channel}:{session_id}:{turn_id}" (counter if no turnId)
    commit_id: int                     # ★ 0, 1, 2… inside the utterance
    seq: int                           # ★ global per-channel counter assigned at creation; playback order is seq order
    channel: Channel
    text_src: str                      # this clause's delta, source language, obvious fillers removed
    prefix_src: str                    # what was committed before in the same utterance (context for Spark)
    boundary: str                      # "punct" | "conj" | "pause" | "speechEnd" | "speechComplete" | "words" | "time"
    t_src_start: float                 # ★ local clock: when it started being said (SPEC §7.4)
    t_src_end: float | None            # local clock: when it ended being said (None if unknown yet)
    t_src_source: str                  # "asr_ts" | "speech_start" | "partial_arrival"
    t_commit: float                    # ★ perf_counter when the commit was created
    text_tgt: str = ""                 # filled by mt/
    remainder: str = ""                # structured-output "remainder" (policy v2); empty in plain LA-2
    t_mt: float | None = None          # ★
    mt_calls: int = 0                  # Spark calls this commit cost (mt_calls_per_commit)
    speculation_hit: bool = False


@dataclass(slots=True)
class PcmChunk:                        # audio ready for the cable; created by tts/, consumed by policy.scheduler
    utterance_id: str
    commit_id: int
    seq: int                           # ★ copied from the Commit
    channel: Channel
    pcm: bytes                         # int16 LE mono at the cable's real sample rate, Edge silences trimmed
    sample_rate: int
    duration_s: float
    t_src_start: float
    t_src_end: float | None
    deadline: float                    # ★ = t_src_start + D; with D = 0 the scheduler reads it as "now, in order"
    stretch_ratio: float = 1.0         # emitted duration / synthesized duration (isochrony)
    t_tts_first: float | None = None   # ★ perf_counter of the first TTS byte received
    t_tts_end: float | None = None
    t_play_start: float | None = None  # ★ written by the scheduler when handing the chunk to the mixer

    @property
    def sync_err_s(self) -> float | None:          # ★ gate S metric
        return None if self.t_play_start is None else self.t_play_start - self.deadline
