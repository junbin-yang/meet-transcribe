"""预下载 FunASR 模型到本地缓存。

运行一次即可，后续启动不再需要网络。
模型总大小约 1.5 GB，下载时间取决于网速（5-15 分钟）。

用法:
    python scripts/pre_download_models.py        # 默认 CPU
    python scripts/pre_download_models.py --cuda  # CUDA 设备
"""

import sys


def main() -> int:
    device = "cuda" if "--cuda" in sys.argv else "cpu"
    print(f"meet-transcribe model pre-download (device={device})")
    print("=" * 50)

    models = [
        ("paraformer-zh", "ASR ~944 MB"),
        ("fsmn-vad", "VAD ~5 MB"),
        ("ct-punc", "Punctuation ~500 MB"),
        ("cam++", "Speaker embedding ~27 MB"),
    ]

    try:
        from funasr import AutoModel
    except ImportError:
        print("ERROR: funasr not installed. Run: pip install funasr")
        return 1

    for model_id, desc in models:
        print(f"\n[{model_id}] {desc} ...")
        try:
            AutoModel(model=model_id, device=device, disable_update=True)
            print(f"  done")
        except Exception as e:
            print(f"  FAIL: {e}")
            return 1

    print("\n" + "=" * 50)
    print("All models cached to ~/.cache/modelscope/hub/models/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
