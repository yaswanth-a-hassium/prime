"""Download the speech, voice and face models Prime needs into the data folder (skips what is already there).

    python fetch_models.py [models folder]      (default: the models folder in Prime's data folder)

The Telugu speech model was converted on the original PC and can't be downloaded: copy the
whisper-telugu-medium-ct2 folder across by USB if you want Telugu. Without it Prime works in English only.
"""
import sys
import urllib.request
from pathlib import Path

import paths

FILES = {
    "wespeaker_en_voxceleb_resnet34.onnx":
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/wespeaker_en_voxceleb_resnet34.onnx",
    "face_detection_yunet_2023mar.onnx":
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "face_recognition_sface_2021dec.onnx":
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
}


def fetch(url, to):
    tmp = to.with_suffix(to.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
        total, done = int(r.headers.get("Content-Length") or 0), 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {to.name}: {done * 100 // total}%", end="", flush=True)
    tmp.replace(to)
    print(f"\r  {to.name}: done      ")


def main():
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else paths.MODELS
    dest.mkdir(parents=True, exist_ok=True)
    print(f"Models go in {dest}")
    for name, url in FILES.items():
        if (dest / name).exists():
            print(f"  {name}: already there")
        else:
            fetch(url, dest / name)
    whisper = dest / "faster-whisper-small"
    if (whisper / "model.bin").exists():
        print("  faster-whisper-small: already there")
    else:
        print("  faster-whisper-small: downloading (about 480 MB)...")
        from huggingface_hub import snapshot_download
        snapshot_download("Systran/faster-whisper-small", local_dir=str(whisper))
        print("  faster-whisper-small: done")
    print("  wake word (Hey Jarvis): ", end="", flush=True)
    from openwakeword import utils
    utils.download_models(["hey_jarvis"])
    print("done")
    if not (dest / "whisper-telugu-medium-ct2").exists():
        print("  Telugu speech model: not installed. Copy the whisper-telugu-medium-ct2 folder from the original PC into\n"
              f"  {dest} if you want Telugu; English works without it.")


if __name__ == "__main__":
    main()
