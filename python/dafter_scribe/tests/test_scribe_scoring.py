from __future__ import annotations

import asyncio
import json
from pathlib import Path

from dafter_core.enums import EventType
from dafter_providers import sarvam
from dafter_scribe.judging import Scorer, judged
from dafter_scribe.plan import language_of
from dafter_scribe.quality import ScribeMetrics, place_of
from dafter_scribe.review import REVIEW_DIR_ENV, ReviewQueue, queue_path, read_queue
from dafter_scribe.sampling import Sampling, sampling_for
from dafter_scribe.scoring import ScoringLoop, scoring_loop
from dafter_scribe.transcript import Transcript
from prometheus_client import CollectorRegistry
from scribe_stub import ASHA, ScriptedLLM, Sent, caption, config, scoring

PASSING = {
    "correctness": "pass",
    "language": "pass",
    "register": "pass",
    "speakability": "pass",
    "reasoning": "ok",
}
FAILING = {**PASSING, "correctness": "fail", "reasoning": "the distance is wrong"}
JUDGE = {"provider": "sarvam", "model": "sarvam-105b"}
RECORDED = {"enabled": True, "consentArtifactId": "consent_rec"}


def scorer(loop: ScoringLoop, *steps: dict[str, str]) -> tuple[Scorer, Sent, ScriptedLLM]:
    model, sent = ScriptedLLM(*steps, fallback=PASSING), Sent()
    return Scorer(model, sarvam.classify, sent, language_of("hi"), 5.0, JUDGE, loop), sent, model


def turns(s: Scorer, count: int) -> None:
    t = Transcript(lambda p: "Asha", "Agent")
    for i in range(count):
        for line in (
            t.heard(caption(f"sg_{2 * i:016x}", f"सवाल {i}?", ASHA)),
            t.heard(caption(f"sg_{2 * i + 1:016x}", f"जवाब {i}।")),
        ):
            assert line is not None
            s.heard(line)


def drain(s: Scorer, sent: Sent, scored: int, kept: Path | None = None) -> None:
    def done() -> bool:
        if len(sent.events) < scored:
            return False
        if kept is None:
            return True
        return kept.is_file() and kept.read_text(encoding="utf-8").endswith("\n")

    async def run() -> None:
        task = asyncio.ensure_future(s.run())
        for _ in range(200):
            if done():
                break
            await asyncio.sleep(0.01)
        task.cancel()

    asyncio.run(run())


def sample(metrics: ScribeMetrics, name: str, **labels: str) -> float:
    place = {"language": "hi", "version": "none", "arm": "stable"}
    return metrics.registry.get_sample_value(name, {**place, **labels}) or 0.0


def test_scoring_is_off_by_default() -> None:
    cfg = config(scribe={"enabled": True})
    assert sampling_for(cfg) == Sampling(0.0, 20)
    assert not sampling_for(cfg).scores_any


def test_a_language_rate_overrides_the_default_rate() -> None:
    cfg = config(scribe={"scoring": {"sampleRate": 0.1, "languageSampleRates": {"hi": 0.5}}})
    assert sampling_for(cfg) == Sampling(0.5, 20)
    other = config(scribe={"scoring": {"sampleRate": 0.1, "languageSampleRates": {"te": 0.5}}})
    assert sampling_for(other).rate == 0.1


def test_sampling_picks_the_same_turns_on_every_replay() -> None:
    sampling = Sampling(0.3, 200)
    segments = [f"sg_{i:016x}" for i in range(2000)]
    picked = [s for s in segments if sampling.picks(s)]
    assert picked == [s for s in segments if sampling.picks(s)]
    assert 500 < len(picked) < 700
    assert not Sampling(0.0, 200).picks(segments[0])
    assert all(Sampling(1.0, 200).picks(s) for s in segments[:50])


def test_an_unsampled_turn_never_reaches_the_judge() -> None:
    metrics = ScribeMetrics(CollectorRegistry())
    s, sent, model = scorer(scoring(rate=0.0, metrics=metrics))
    turns(s, 3)
    drain(s, sent, 0)
    assert model.requests == [] and sent.events == []
    assert sample(metrics, "dafter_scribe_turns_total", outcome="unsampled") == 3


def test_the_cap_bounds_what_one_session_spends_on_the_judge() -> None:
    metrics = ScribeMetrics(CollectorRegistry())
    s, sent, model = scorer(scoring(cap=2, metrics=metrics))
    turns(s, 5)
    drain(s, sent, 2)
    assert len(model.requests) == 2 and len(sent.of(EventType.AGENT_TURN_SCORED)) == 2
    assert sample(metrics, "dafter_scribe_turns_total", outcome="capped") == 3
    assert sample(metrics, "dafter_scribe_turns_total", outcome="scored") == 2


def test_verdicts_are_counted_by_criterion() -> None:
    metrics = ScribeMetrics(CollectorRegistry())
    s, sent, _ = scorer(scoring(metrics=metrics), FAILING, PASSING)
    turns(s, 2)
    drain(s, sent, 2)
    verdicts = "dafter_scribe_verdicts_total"
    assert sample(metrics, verdicts, criterion="correctness", verdict="fail") == 1
    assert sample(metrics, verdicts, criterion="correctness", verdict="pass") == 1
    assert sample(metrics, verdicts, criterion="register", verdict="pass") == 2
    assert sample(metrics, verdicts, criterion="speakability", verdict="maybe") == 0
    assert sample(metrics, "dafter_scribe_turn_score_count") == 2


def test_metrics_are_labelled_by_the_version_and_arm_the_session_runs() -> None:
    cfg = config(version={"id": "support-v4", "candidate": True})
    assert place_of(cfg) == ("hi", "support-v4", "candidate")
    assert place_of(config(version=None)) == ("hi", "none", "stable")


def test_the_judge_reads_the_filled_bank_for_its_language() -> None:
    for tag in ("hi", "en-IN", "te-IN", "kn-IN", "mr-IN"):
        found = judged(language_of(tag))
        assert found.questions and found.name == language_of(tag).name


def test_a_failed_turn_is_kept_for_review_with_its_consented_audio(tmp_path: Path) -> None:
    cfg = config(recording=RECORDED, version={"id": "support-v4", "candidate": True})
    queue = ReviewQueue(queue_path(tmp_path, cfg.language, cfg.session_id))
    s, sent, _ = scorer(scoring(queue=queue, cfg=cfg), FAILING, PASSING)
    turns(s, 2)
    drain(s, sent, 2, queue.path)
    [kept] = list(read_queue(tmp_path, "hi"))
    assert kept.segment_id == "sg_0000000000000001" and kept.failed == ("correctness",)
    assert (kept.question, kept.reply) == ("सवाल 0?", "जवाब 0।")
    assert kept.config_version == {"id": "support-v4", "arm": "candidate"}
    assert kept.audio is not None and kept.audio.consent_id == "consent_rec"
    assert kept.audio.participant_ids == (ASHA,)
    assert kept.audio.segment_ids == ("sg_0000000000000000",)


def test_a_failed_turn_without_recording_consent_keeps_no_audio(tmp_path: Path) -> None:
    cfg = config()
    queue = ReviewQueue(queue_path(tmp_path, cfg.language, cfg.session_id))
    s, sent, _ = scorer(scoring(queue=queue, cfg=cfg), FAILING)
    turns(s, 1)
    drain(s, sent, 1, queue.path)
    [kept] = list(read_queue(tmp_path, "hi"))
    assert kept.audio is None
    assert "audio" not in json.loads(queue.path.read_text(encoding="utf-8"))


def test_the_queue_needs_both_the_session_flag_and_the_worker_folder(tmp_path: Path) -> None:
    on = config(scribe={"scoring": {"keepFailures": True}})
    folder = {REVIEW_DIR_ENV: str(tmp_path)}
    assert scoring_loop(config(), env=folder).queue is None
    assert scoring_loop(on, env={}).queue is None
    loop = scoring_loop(on, env=folder)
    assert loop.queue is not None
    assert loop.queue.path == tmp_path / "hi" / f"{on.session_id}.jsonl"
