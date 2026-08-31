from __future__ import annotations

import ctypes
import wave
from ctypes import wintypes
from pathlib import Path


WAVE_FORMAT_PCM = 1
CALLBACK_NULL = 0
WAVE_MAPPER = -1
MMSYSERR_NOERROR = 0
WHDR_DONE = 0x00000001


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


class WAVEHDR(ctypes.Structure):
    _fields_ = [
        ("lpData", ctypes.c_char_p),
        ("dwBufferLength", wintypes.DWORD),
        ("dwBytesRecorded", wintypes.DWORD),
        ("dwUser", ctypes.c_size_t),
        ("dwFlags", wintypes.DWORD),
        ("dwLoops", wintypes.DWORD),
        ("lpNext", ctypes.c_void_p),
        ("reserved", ctypes.c_size_t),
    ]


class WindowsWaveRecorder:
    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        channels: int = 1,
        bits_per_sample: int = 16,
        buffer_ms: int = 250,
        buffer_count: int = 96,
    ) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.bits_per_sample = bits_per_sample
        self.buffer_ms = buffer_ms
        self.buffer_count = buffer_count
        self._winmm = ctypes.WinDLL("winmm")
        self._handle = ctypes.c_void_p()
        self._headers: list[WAVEHDR] = []
        self._buffers: list[ctypes.Array[ctypes.c_char]] = []
        self._recording = False

    def start(self) -> None:
        if self._recording:
            return

        block_align = self.channels * self.bits_per_sample // 8
        fmt = WAVEFORMATEX(
            WAVE_FORMAT_PCM,
            self.channels,
            self.sample_rate,
            self.sample_rate * block_align,
            block_align,
            self.bits_per_sample,
            0,
        )
        result = self._winmm.waveInOpen(
            ctypes.byref(self._handle),
            WAVE_MAPPER,
            ctypes.byref(fmt),
            0,
            0,
            CALLBACK_NULL,
        )
        if result != MMSYSERR_NOERROR:
            raise RuntimeError(f"Could not open microphone. Windows audio error {result}.")

        buffer_size = int(self.sample_rate * block_align * (self.buffer_ms / 1000))
        self._headers = []
        self._buffers = []
        header_size = ctypes.sizeof(WAVEHDR)
        for _ in range(self.buffer_count):
            buffer = ctypes.create_string_buffer(buffer_size)
            header = WAVEHDR(
                ctypes.cast(buffer, ctypes.c_char_p),
                buffer_size,
                0,
                0,
                0,
                0,
                None,
                0,
            )
            self._check(self._winmm.waveInPrepareHeader(self._handle, ctypes.byref(header), header_size))
            self._check(self._winmm.waveInAddBuffer(self._handle, ctypes.byref(header), header_size))
            self._buffers.append(buffer)
            self._headers.append(header)

        self._check(self._winmm.waveInStart(self._handle))
        self._recording = True

    def stop_to_file(self, output_path: Path) -> Path:
        if not self._recording:
            raise RuntimeError("Recorder is not running.")

        self._recording = False
        self._winmm.waveInStop(self._handle)
        self._winmm.waveInReset(self._handle)

        chunks: list[bytes] = []
        header_size = ctypes.sizeof(WAVEHDR)
        for header, buffer in zip(self._headers, self._buffers):
            if header.dwFlags & WHDR_DONE and header.dwBytesRecorded:
                chunks.append(bytes(buffer.raw[: header.dwBytesRecorded]))
            self._winmm.waveInUnprepareHeader(self._handle, ctypes.byref(header), header_size)

        self._winmm.waveInClose(self._handle)
        self._handle = ctypes.c_void_p()
        self._headers = []
        self._buffers = []

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output_path), "wb") as wav_file:
            wav_file.setnchannels(self.channels)
            wav_file.setsampwidth(self.bits_per_sample // 8)
            wav_file.setframerate(self.sample_rate)
            wav_file.writeframes(b"".join(chunks))
        return output_path

    def abort(self) -> None:
        if not self._recording:
            return
        self._recording = False
        try:
            self._winmm.waveInReset(self._handle)
            header_size = ctypes.sizeof(WAVEHDR)
            for header in self._headers:
                self._winmm.waveInUnprepareHeader(self._handle, ctypes.byref(header), header_size)
            self._winmm.waveInClose(self._handle)
        finally:
            self._handle = ctypes.c_void_p()
            self._headers = []
            self._buffers = []

    @staticmethod
    def _check(result: int) -> None:
        if result != MMSYSERR_NOERROR:
            raise RuntimeError(f"Windows audio error {result}.")
