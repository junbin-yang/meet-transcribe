"""M2a 验收脚本：注册 + top-1 准确率测试。

用法：
    python scripts/m2a_verify.py \
        --enroll-dir data/aishell4/enroll \
        --query-dir  data/aishell4/query \
        --base-url http://127.0.0.1:8080 \
        --api-key  $MT_TEST_API_KEY \
        --threshold 0.65 \
        --min-accuracy 0.80

目录约定：
    enroll/<speaker_name>/*.wav   (≥1 个，第一个 POST /v1/speakers，其余追加 samples)
    query /<speaker_name>/*.wav   (任意个，每个跑一次本地 ECAPA + 服务端最近邻)

退出码：
    0 = top-1 ≥ min-accuracy（验收通过）
    2 = 准确率不达标
    3 = 注册阶段失败（HTTP 错误 / 422 SNR 不达标）

输出：
    最后一行打印 JSON {n_speakers, total_queries, correct, top1_accuracy,
                       threshold, per_speaker: {name: {n, correct}}}

Fact-Forcing Gate facts:
  1. Callers: 命令行 `python scripts/m2a_verify.py --enroll-dir ... --query-dir ...`
  2. No duplicate: Glob scripts/*.py → 无现存 .py 文件
  3. Data fields: 读取 <dir>/<speaker>/*.wav，输出 JSON {top1_accuracy 等}
  4. User verbatim: 用户原话 "开始" — M2a 验收脚本（≥80% top-1）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import requests

from meet_transcribe.speakers.embedding import (
    DecodedAudio,
    QualityRejected,
    compute_embedding,
    decode_audio,
)


def _iter_wavs(root: Path) -> dict[str, list[Path]]:
    """收集 root/<speaker>/*.wav。"""
    result: dict[str, list[Path]] = {}
    for sub in sorted(root.iterdir()):
        if not sub.is_dir():
            continue
        wavs = sorted(p for p in sub.iterdir() if p.suffix.lower() in {".wav", ".flac"})
        if wavs:
            result[sub.name] = wavs
    return result


def _local_embedding(
    path: Path, *, model_name: str, min_sample_seconds: float, min_snr_db: float
) -> np.ndarray:
    """本地跑 ECAPA，得到查询向量。"""
    raw = path.read_bytes()
    audio: DecodedAudio = decode_audio(raw)
    res = compute_embedding(
        audio,
        model_name=model_name,
        min_sample_seconds=min_sample_seconds,
        min_snr_db=min_snr_db,
    )
    return res.embedding


def _enroll(
    *,
    base_url: str,
    api_key: str,
    enroll_map: dict[str, list[Path]],
) -> dict[str, str]:
    """逐人注册到服务端，返回 name -> speaker_id（字符串 UUID）。"""
    headers = {"Authorization": f"Bearer {api_key}"}
    name_to_id: dict[str, str] = {}

    for name, wavs in enroll_map.items():
        first = wavs[0]
        with first.open("rb") as fh:
            resp = requests.post(
                f"{base_url}/v1/speakers",
                headers=headers,
                data={"name": name, "consent_source": "m2a_verify"},
                files={"audio_file": (first.name, fh, "audio/wav")},
                timeout=120,
            )
        if resp.status_code >= 400:
            print(
                f"[enroll-fail] {name} {first.name} -> "
                f"{resp.status_code} {resp.text[:200]}",
                file=sys.stderr,
            )
            sys.exit(3)
        sid = resp.json()["id"]
        name_to_id[name] = sid

        for extra in wavs[1:]:
            with extra.open("rb") as fh:
                add = requests.post(
                    f"{base_url}/v1/speakers/{sid}/samples",
                    headers=headers,
                    files={"audio_file": (extra.name, fh, "audio/wav")},
                    timeout=120,
                )
            if add.status_code >= 400:
                print(
                    f"[sample-fail] {name} {extra.name} -> "
                    f"{add.status_code} {add.text[:200]}",
                    file=sys.stderr,
                )
                sys.exit(3)

    return name_to_id


async def _load_server_embeddings(
    name_to_id: dict[str, str],
) -> dict[str, np.ndarray]:
    """直连 PG 拉服务端实际存储的均值向量。"""
    from sqlalchemy import select

    from meet_transcribe.config.loader import load_config
    from meet_transcribe.db.models import Speaker
    from meet_transcribe.db.session import dispose, init_engine_from_config

    cfg = load_config()
    engine = init_engine_from_config(cfg)
    try:
        ids = [uuid.UUID(v) for v in name_to_id.values()]
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    select(Speaker.id, Speaker.embedding).where(Speaker.id.in_(ids))
                )
            ).all()
        id_to_vec = {str(r[0]): np.asarray(r[1], dtype=np.float32) for r in rows}
        return {name: id_to_vec[sid] for name, sid in name_to_id.items() if sid in id_to_vec}
    finally:
        await dispose()


def _evaluate(
    *,
    name_to_id: dict[str, str],
    server_embeds: dict[str, np.ndarray],
    query_map: dict[str, list[Path]],
    model_name: str,
    min_sample_seconds: float,
    min_snr_db: float,
    threshold: float,
) -> dict[str, Any]:
    """对每个查询样本：本地算向量，与服务端各 speaker 均值算余弦相似度，取 top-1。"""
    names = sorted(server_embeds.keys())
    if not names:
        return {
            "n_speakers": 0,
            "total_queries": 0,
            "correct": 0,
            "top1_accuracy": 0.0,
            "threshold": threshold,
            "per_speaker": {},
        }
    matrix = np.stack([server_embeds[n] for n in names], axis=0)  # [N, 192]

    per_speaker: dict[str, dict[str, int]] = {n: {"n": 0, "correct": 0} for n in names}
    total = 0
    correct = 0

    for true_name, wavs in query_map.items():
        if true_name not in per_speaker:
            continue
        for wav in wavs:
            try:
                q = _local_embedding(
                    wav,
                    model_name=model_name,
                    min_sample_seconds=min_sample_seconds,
                    min_snr_db=min_snr_db,
                )
            except QualityRejected as e:
                print(f"[query-skip] {true_name} {wav.name} -> {e.reason}", file=sys.stderr)
                continue
            sims = matrix @ q  # 双方都已 L2 归一化，点积即余弦
            best_idx = int(np.argmax(sims))
            best_sim = float(sims[best_idx])
            pred_name = names[best_idx] if best_sim >= threshold else None
            total += 1
            per_speaker[true_name]["n"] += 1
            if pred_name == true_name:
                correct += 1
                per_speaker[true_name]["correct"] += 1

    accuracy = (correct / total) if total > 0 else 0.0
    return {
        "n_speakers": len(names),
        "total_queries": total,
        "correct": correct,
        "top1_accuracy": round(accuracy, 4),
        "threshold": threshold,
        "per_speaker": per_speaker,
    }


def _reset_speakers(*, base_url: str, api_key: str) -> None:
    """删掉当前 tenant 下所有已注册 speaker（软删除）。"""
    headers = {"Authorization": f"Bearer {api_key}"}
    resp = requests.get(f"{base_url}/v1/speakers", headers=headers, timeout=30)
    resp.raise_for_status()
    for row in resp.json():
        sid = row["id"]
        d = requests.delete(
            f"{base_url}/v1/speakers/{sid}", headers=headers, timeout=30
        )
        if d.status_code not in (204, 404):
            print(f"[reset-warn] delete {sid} -> {d.status_code}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="M2a 声纹注册 / top-1 验收")
    parser.add_argument("--enroll-dir", required=True, type=Path)
    parser.add_argument("--query-dir", required=True, type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--threshold", type=float, default=0.65)
    parser.add_argument("--min-accuracy", type=float, default=0.80)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="先软删除当前 tenant 下所有 speaker 再注册",
    )
    args = parser.parse_args()

    from meet_transcribe.config.loader import load_config

    cfg = load_config().speakers

    enroll_map = _iter_wavs(args.enroll_dir)
    query_map = _iter_wavs(args.query_dir)
    if not enroll_map:
        print(f"no enroll subdirs under {args.enroll_dir}", file=sys.stderr)
        return 3

    if args.reset:
        _reset_speakers(base_url=args.base_url, api_key=args.api_key)

    name_to_id = _enroll(
        base_url=args.base_url, api_key=args.api_key, enroll_map=enroll_map
    )
    server_embeds = asyncio.run(_load_server_embeddings(name_to_id))

    report = _evaluate(
        name_to_id=name_to_id,
        server_embeds=server_embeds,
        query_map=query_map,
        model_name=cfg.embedding_model,
        min_sample_seconds=cfg.min_sample_seconds,
        min_snr_db=cfg.min_snr_db,
        threshold=args.threshold,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if report["top1_accuracy"] >= args.min_accuracy:
        return 0
    print(
        f"[fail] top1 {report['top1_accuracy']:.2%} < required {args.min_accuracy:.2%}",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
