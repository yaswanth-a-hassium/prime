"""Prime's voice: a deep, metallic robot effect laid over an ordinary text-to-speech voice.

Nothing here imitates a real person's voice: it is plain signal processing on whatever TTS voice is chosen.

    pitch down    a phase-vocoder pitch shift (duration kept), -5 semitones by default
    sub layer     a quieter copy an octave further down, for size
    vocoder       a channel vocoder: the speech's spectral envelope imposed on a buzzy sawtooth + noise carrier,
                  which gives the flat, synthetic "machine" timbre
    metal         a short feedback comb (a few ms) and a touch of ring modulation: the hollow, metallic ring

    apply(pcm_int16, rate, depth=0.7, semitones=-5) -> int16 numpy array, same length

depth 0 returns the pitched-down voice only; 1 is the full effect. Runs in well under a second for a spoken reply.
"""
import numpy as np
from scipy.signal import lfilter, resample

N_FFT = 1024
HOP = 256
_WIN = np.hanning(N_FFT).astype(np.float32)


def _stft(x):
    pad = np.concatenate([np.zeros(N_FFT // 2, np.float32), x, np.zeros(N_FFT, np.float32)])
    n = 1 + (len(pad) - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n)[:, None]
    return np.fft.rfft(pad[idx] * _WIN, axis=1)


def _istft(spec, length):
    frames = np.fft.irfft(spec, n=N_FFT, axis=1) * _WIN
    out = np.zeros(HOP * (len(frames) - 1) + N_FFT, np.float32)
    norm = np.zeros_like(out)
    for i, f in enumerate(frames):
        out[i * HOP:i * HOP + N_FFT] += f
        norm[i * HOP:i * HOP + N_FFT] += _WIN ** 2
    out /= np.maximum(norm, 1e-6)
    out = out[N_FFT // 2:N_FFT // 2 + length]
    return np.pad(out, (0, max(0, length - len(out))))


def _stretch(x, rate):
    """Phase-vocoder time stretch: rate > 1 shortens, < 1 lengthens; pitch is unchanged."""
    spec = _stft(x)
    steps = np.arange(0, len(spec) - 1, rate)
    expected = 2 * np.pi * HOP * np.arange(spec.shape[1]) / N_FFT
    phase = np.angle(spec[0])
    out = np.empty((len(steps), spec.shape[1]), np.complex64)
    for i, t in enumerate(steps):
        k = int(t)
        a, b = spec[k], spec[k + 1]
        frac = t - k
        mag = (1 - frac) * np.abs(a) + frac * np.abs(b)
        out[i] = mag * np.exp(1j * phase)
        dphi = np.angle(b) - np.angle(a) - expected
        dphi -= 2 * np.pi * np.round(dphi / (2 * np.pi))
        phase = phase + expected + dphi
    return _istft(out, int(round(len(x) / rate)))


def pitch_shift(x, semitones):
    """x (float32) shifted by `semitones`, same length."""
    if abs(semitones) < 0.05 or len(x) < N_FFT:
        return x
    f = 2.0 ** (semitones / 12.0)                 # < 1 for down
    longer = resample(x, int(round(len(x) / f))).astype(np.float32)   # lower and slower
    out = _stretch(longer, 1.0 / f)               # back to the original speed, pitch stays low
    return _fit(out, len(x))


def _fit(x, n):
    return x[:n] if len(x) >= n else np.pad(x, (0, n - len(x)))


def _band_envelope(mag, bands=28):
    """Spectral envelope per frame: magnitudes averaged over log-spaced bands, spread back over the bins."""
    nbins = mag.shape[1]
    edges = np.unique(np.round(np.geomspace(2, nbins - 1, bands + 1)).astype(int))
    env = np.empty_like(mag)
    env[:, :edges[0]] = mag[:, :edges[0]].mean(axis=1, keepdims=True)
    for lo, hi in zip(edges[:-1], edges[1:]):
        env[:, lo:hi] = mag[:, lo:hi].mean(axis=1, keepdims=True)
    env[:, edges[-1]:] = mag[:, edges[-1]:].mean(axis=1, keepdims=True)
    return env


def vocode(modulator, rate, f0=58.0):
    """Channel vocoder: the modulator's band envelopes on a sawtooth (plus a little noise) carrier."""
    n = len(modulator)
    t = np.arange(n, dtype=np.float32) / rate
    saw = 2.0 * ((t * f0) % 1.0) - 1.0
    saw += 0.5 * (2.0 * ((t * f0 * 2.01) % 1.0) - 1.0)            # a detuned octave: thicker buzz
    noise = np.random.default_rng(108).standard_normal(n).astype(np.float32) * 0.25
    carrier = (saw + noise).astype(np.float32)
    m, c = _stft(modulator), _stft(carrier)
    m_env = _band_envelope(np.abs(m))
    c_env = _band_envelope(np.abs(c)) + 1e-6
    out = c / c_env * m_env                                          # whitened carrier, shaped like the speech
    return _istft(out.astype(np.complex64), n)


def metal(x, rate, delay_ms=4.5, feedback=0.55, ring_hz=32.0, ring_mix=0.18):
    """Hollow metallic ring: a short feedback comb plus a touch of ring modulation."""
    d = max(1, int(rate * delay_ms / 1000))
    a = np.zeros(d + 1, np.float32)
    a[0], a[d] = 1.0, -feedback
    combed = lfilter([1.0 - feedback], a, x).astype(np.float32)
    t = np.arange(len(x), dtype=np.float32) / rate
    return (1 - ring_mix) * combed + ring_mix * combed * np.sin(2 * np.pi * ring_hz * t)


def _rms(x):
    return float(np.sqrt(np.mean(x * x))) if len(x) else 0.0


def _match(x, ref):
    r = _rms(x)
    return x * (_rms(ref) / r) if r > 1e-9 else x


def apply(pcm, rate, depth=0.7, semitones=-5.0):
    """The robot voice for one clip of int16 mono audio. Returns int16, same length."""
    if pcm is None or len(pcm) == 0:
        return pcm
    x = pcm.astype(np.float32) / 32768.0
    depth = float(min(1.0, max(0.0, depth)))
    low = pitch_shift(x, semitones)
    if depth <= 0.001:
        y = low
    else:
        sub = _match(pitch_shift(x, semitones - 12), low)
        vox = _match(vocode(low, rate), low)
        ring = _match(metal(low, rate), low)
        y = (1 - 0.55 * depth) * low + 0.30 * depth * sub + 0.45 * depth * vox + 0.35 * depth * ring
        # a gentle high cut: the vocoder's top end is fizzy
        y = lfilter([0.6, 0.4], [1.0], y).astype(np.float32)
    peak = float(np.max(np.abs(y))) or 1.0
    y = y * (0.89 / peak)
    return np.clip(y * 32767.0, -32768, 32767).astype(np.int16)
