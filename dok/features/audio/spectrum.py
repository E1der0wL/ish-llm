"""PCM format, frequency bands and normalized spectrum interpretation."""

import cmath
import math
import struct


class SpectrumAnalyzer:
    SAMPLE_RATE = 48000
    CHANNELS = 2
    SAMPLE_FORMAT = "s16le"
    SAMPLE_SCALE = 32768
    FRAMES = 2048
    BANDS = 24
    MIN_HZ = 40.0
    FLOOR_DB = -60.0
    PCM = struct.Struct(f"<{FRAMES * CHANNELS}h")

    def __init__(self):
        self._window = tuple(0.5 - 0.5 * math.cos(2 * math.pi * n / (self.FRAMES - 1))
                             for n in range(self.FRAMES))
        self._scale = 2 / sum(self._window)
        ratio = (self.SAMPLE_RATE / 2 / self.MIN_HZ) ** (1 / self.BANDS)
        self._ranges = tuple((max(1, int(self.MIN_HZ * ratio ** band * self.FRAMES / self.SAMPLE_RATE)),
                              min(self.FRAMES // 2 + 1,
                                  max(2, math.ceil(self.MIN_HZ * ratio ** (band + 1) * self.FRAMES / self.SAMPLE_RATE))))
                             for band in range(self.BANDS))

    @staticmethod
    def _fft(values):
        size = len(values)
        result = list(map(complex, values))
        j = 0
        for i in range(1, size):
            bit = size >> 1
            while j & bit:
                j ^= bit
                bit >>= 1
            j ^= bit
            if i < j:
                result[i], result[j] = result[j], result[i]
        length = 2
        while length <= size:
            step = cmath.exp(-2j * math.pi / length)
            for start in range(0, size, length):
                factor = 1
                for offset in range(length // 2):
                    even = result[start + offset]
                    odd = result[start + offset + length // 2] * factor
                    result[start + offset] = even + odd
                    result[start + offset + length // 2] = even - odd
                    factor *= step
            length <<= 1
        return result

    @classmethod
    def silence(cls) -> tuple[float, ...]:
        return (0.0,) * cls.BANDS

    @classmethod
    def combine(cls, frames) -> tuple[float, ...]:
        frames = tuple(frames)
        return tuple(max(frame[band] for frame in frames) for band in range(cls.BANDS)) if frames else cls.silence()

    def analyze(self, pcm: bytes) -> tuple[float, ...]:
        samples = self.PCM.unpack(pcm)
        if not any(samples):
            return self.silence()
        spectra = [self._fft([sample / self.SAMPLE_SCALE * window
                              for sample, window in zip(samples[channel::self.CHANNELS], self._window)])
                   for channel in range(self.CHANNELS)]
        levels = []
        for low, high in self._ranges:
            magnitude = max(abs(spectrum[index]) for spectrum in spectra for index in range(low, max(low + 1, high)))
            db = 20 * math.log10(max(magnitude * self._scale, 10 ** (self.FLOOR_DB / 20)))
            levels.append(min(1.0, max(0.0, (db - self.FLOOR_DB) / -self.FLOOR_DB)))
        return tuple(levels)
