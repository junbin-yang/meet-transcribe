"""测试 CAM++ 声纹匹配：cc_ys1.wav vs cc_ys1_tts.wav"""
import asyncio, sys, uuid
import numpy as np

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from meet_transcribe.speakers.embedding import decode_audio, compute_embedding
from meet_transcribe.speakers.matcher import match
from meet_transcribe.config.loader import load_config
from meet_transcribe.db.session import init_engine_from_config, get_session_factory
from sqlalchemy import text


async def main():
    cfg = load_config()

    # Extract embeddings
    with open("tests/assets/cc_ys1.wav", "rb") as f:
        raw1 = f.read()
    a1 = decode_audio(raw1)
    e1 = compute_embedding(a1)
    print(f"cc_ys1.wav:      {a1.duration_s:.1f}s, emb={e1.embedding.shape}")

    with open("tests/assets/cc_ys1_tts.wav", "rb") as f:
        raw2 = f.read()
    a2 = decode_audio(raw2)
    e2 = compute_embedding(a2)
    print(f"cc_ys1_tts.wav:  {a2.duration_s:.1f}s, emb={e2.embedding.shape}")

    # Direct cosine similarity
    sim = float(np.dot(e1.embedding, e2.embedding))
    print(f"\nDirect cosine similarity: {sim:.4f} ({'MATCH' if sim >= 0.65 else 'BELOW THRESHOLD'})")

    # Check DB
    engine = init_engine_from_config(cfg)
    async with engine.connect() as conn:
        rows = (await conn.execute(
            text("SELECT id, name, tenant_id FROM speakers WHERE deleted_at IS NULL")
        )).fetchall()
        print(f"DB speakers: {len(rows)}")
        for r in rows:
            print(f"  id={r[0]}, name={r[1]}, tenant={r[2]}")

    # Match via matcher
    if rows:
        tid = rows[0][2]
        factory = get_session_factory()
        async with factory() as db:
            r1 = await match(db, tenant_id=tid, query=e1.embedding, threshold=0.65)
            print(f"\ncc_ys1.wav → DB:    matched={r1.matched}, name={r1.name}, score={r1.score:.4f}")
            r2 = await match(db, tenant_id=tid, query=e2.embedding, threshold=0.65)
            print(f"cc_ys1_tts.wav → DB: matched={r2.matched}, name={r2.name}, score={r2.score:.4f}")


asyncio.run(main())
