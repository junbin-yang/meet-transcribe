"""POST /v1/transcribe/file — 离线批转写。"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, File, Form, UploadFile

from meet_transcribe.auth.api_key import TenantContext, require_tenant
from meet_transcribe.config.loader import load_config
from meet_transcribe.core.funasr_adapter import init_spk_engine
from meet_transcribe.observability.logging import get_logger

router = APIRouter()
log = get_logger(__name__)


@router.post("/transcribe/file")
async def transcribe_file(
    file: UploadFile = File(...),
    language: str = Form("auto"),
    _tenant: TenantContext = Depends(require_tenant),
) -> dict:
    """上传音频文件，返回带说话人标签的转写结果。

    支持的格式：WAV / FLAC / OGG / MP3 / M4A。
    返回 sentence_info 数组，每项含 text / speaker / start / end。
    """
    raw = await file.read()
    log.info("transcribe.file.start", filename=file.filename, size=len(raw))

    from meet_transcribe.speakers.embedding import decode_audio, AudioDecodeFailed

    try:
        decoded = await asyncio.to_thread(decode_audio, raw)
    except AudioDecodeFailed as e:
        return {"error": "decode_failed", "message": str(e)}

    cfg = load_config()
    engine = init_spk_engine(cfg.asr.device, asr_model=cfg.asr.model)

    import numpy as np

    result = await asyncio.to_thread(
        engine.generate,
        input=decoded.samples,
        input_fs=16000,
        language=language,
        batch_size_s=300,
    )

    sentences: list[dict] = []
    text = ""
    if isinstance(result, list) and result:
        r0 = result[0]
        if isinstance(r0, dict):
            text = str(r0.get("text", ""))
            for si in r0.get("sentence_info", []) or []:
                if isinstance(si, dict):
                    sentences.append({
                        "text": str(si.get("text", "")),
                        "speaker": int(si.get("spk", 0)) + 1,
                        "start": float(si.get("start", 0)) / 1000.0,
                        "end": float(si.get("end", 0)) / 1000.0,
                    })

    # Voiceprint matching per speaker
    if sentences:
        await _resolve_speakers(sentences, decoded.samples, _tenant.tenant_id)

    log.info(
        "transcribe.file.done",
        filename=file.filename,
        sentences=len(sentences),
        speakers=len({s["speaker"] for s in sentences}),
    )

    return {
        "text": text,
        "sentences": sentences,
        "duration_s": decoded.duration_s,
    }


async def _resolve_speakers(
    sentences: list[dict], audio: "np.ndarray", tenant_id: "uuid.UUID"
) -> None:
    """对每个 speaker 提取 CAM++ embedding 并与 pgvector 匹配。"""
    import uuid

    import numpy as np

    from meet_transcribe.speakers.embedding import (
        DecodedAudio,
        QualityRejected,
        compute_embedding,
    )
    from meet_transcribe.speakers.matcher import match
    from meet_transcribe.db.session import get_session_factory

    unique_speakers = {s["speaker"] for s in sentences}
    try:
        factory = get_session_factory()
    except RuntimeError:
        return

    for spk in unique_speakers:
        spk_sentences = [s for s in sentences if s["speaker"] == spk]
        total_s = sum(s["end"] - s["start"] for s in spk_sentences)
        if total_s < 3.0:
            continue

        chunks: list[np.ndarray] = []
        for s in spk_sentences:
            s_idx = int(max(0, s["start"] * 16000))
            e_idx = int(min(len(audio), s["end"] * 16000))
            if e_idx > s_idx:
                chunks.append(audio[s_idx:e_idx])
        if not chunks:
            continue
        spk_audio = np.concatenate(chunks)

        try:
            emb = compute_embedding(
                DecodedAudio(samples=spk_audio, duration_s=float(len(spk_audio) / 16000)),
                min_sample_seconds=1.0,
                min_snr_db=0.0,
            )
        except QualityRejected:
            continue

        async with factory() as db:
            result_match = await match(
                db, tenant_id=tenant_id, query=emb.embedding, threshold=0.65
            )
        if result_match.matched:
            for s in sentences:
                if s["speaker"] == spk:
                    s["speaker_resolved"] = {
                        "id": str(result_match.speaker_id),
                        "name": result_match.name,
                        "score": round(float(result_match.score), 4),
                    }
