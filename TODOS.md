# TODOS.md — meet-transcribe

## Active

### ~~TODO: FunASR AutoModel 线程安全预研 spike~~ DONE
- **Priority:** P0
- **Status:** DONE (2026-05-27)
- **Result:** AutoModel IS thread-safe. 3 concurrent ASR sessions + 3 concurrent ASR+Speaker sessions all pass. Model IDs verified: `iic/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-pytorch` (combined ASR+VAD+Punc, 859MB) + `iic/speech_campplus_sv_zh-cn_16k-common` (CAM++ speaker, 27MB). CPU RTF: 0.15-0.22.

### ~~TODO: install.sh 预下载 FunASR 模型到离线缓存~~ DONE (dev)
- **Priority:** P0
- **Status:** DONE on dev (models cached by spike). Deploy script update deferred to M2.
- **Result:** Both models cached at `~/.cache/modelscope/hub/models/`. install.sh update tracked separately for Linux deployment.

### TODO: CAM++ vs ECAPA embedding 质量 A/B 评估
- **Priority:** P1
- **Effort:** S (CC ~15min)
- **Created:** 2026-05-27
- **Why:** ECAPA-TDNN → CAM++ 替换后，现有 speaker gallery 的匹配准确率必须不下降。
- **Acceptance:** test_campp_vs_ecapa.py 通过，准确率差异 < 5%。
- **Depends on:** FunASR CAM++ 模型加载成功

## Done (2026-05-27)

### ~~FunASR 迁移核心实现~~
- **Status:** DONE
- **Changes:**
  - `funasr_adapter.py` — 新适配层（FunASR AutoModel 包装）
  - `orchestrator.py` — 重写为 FunASR 流式引擎
  - `ws.py` — 切换导入 + `FunASRSpec`
  - `embedding.py` — ECAPA-TDNN → CAM++（FunASR）
  - `pyproject.toml` — funasr/modelscope 替换 faster-whisper/speechbrain
  - `TODOS.md` — 创建并跟踪进度
- **Verification:** FastAPI app loads (16 routes), all modules import OK

## Remaining (M2)

### Clean up vendored/whisperlivekit + whisperlivekit_adapter.py
- **Priority:** P1
- **Depends on:** FunASR passing integration tests and demo verification
- **Why:** Dead code removal after migration validated

### Update tests
- **Priority:** P1
- **Depends on:** FunASR integration working end-to-end
- **Changes:** Replace whisper tests with funasr equivalents
