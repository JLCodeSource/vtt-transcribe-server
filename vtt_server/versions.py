"""Versions of a job's output: the original text, every translation, every diarization. Nothing is ever dropped.

A completed job holds, in plain JSON-able structures (so they persist as they are):

``segments``
    The immutable original: ``[{"start", "end", "text"}]`` in the spoken language. Never edited, always exportable.
``translations``
    ``{language: {"texts": [one per segment], "engine", "source", "created_at"}}``. Translating to another language
    *adds* an entry; the original and the other languages are untouched.
``diarizations`` / ``active_diarization``
    ``[{"id", "label", "num_speakers", "method", "created_at", "turns", "labels", "names"}]``. ``turns`` are the
    diarizer's raw output (kept so a version can be re-derived without the model); ``labels`` is the speaker id per
    segment (what merge and split edit); ``names`` the display names. Every run adds a version; edits touch only the
    active one; switching the active version is free.

``result`` stays a rendering of the original text with the active diarization's names (it is what listings and the
legacy export use). Retention and deletion act on the whole job, so every version goes together.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from vtt_transcribe import speakers

SCHEMA = 2
Segment = dict[str, Any]
Turns = list[tuple[float, float, str]]


class VersionError(ValueError):
    """An unknown or invalid version (language, diarization)."""


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ------------------------------------------------------------------------------ original text


def base_segments(segments: list[Segment]) -> list[Segment]:
    """The immutable original: only time range and text (no speaker, no translation)."""
    return [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in segments]


def set_original(job: dict[str, Any], segments: list[Segment]) -> None:
    """Store the spoken-language transcript. Called once, when the job is transcribed."""
    job["segments"] = base_segments(segments)
    job.setdefault("translations", {})
    job.setdefault("diarizations", [])
    job.setdefault("active_diarization", None)
    job["schema"] = SCHEMA
    refresh_result(job)


def has_versions(job: dict[str, Any]) -> bool:
    return job.get("schema") == SCHEMA and bool(job.get("segments"))


# ------------------------------------------------------------------------------ translations


def find_language(job: dict[str, Any], language: str | None) -> str | None:
    """The stored key for ``language`` (case-insensitive); ``None`` for the original; ``VersionError`` if unknown."""
    if language is None or not language.strip() or language.strip().casefold() == "original":
        return None
    wanted = language.strip().casefold()
    for key in job.get("translations", {}):
        if key.casefold() == wanted:
            return key
    msg = f"No translation to {language.strip()!r}"
    raise VersionError(msg)


def latest_language(job: dict[str, Any]) -> str | None:
    """The most recently added translation (what ``translated_to`` has always meant), or ``None``."""
    translations = job.get("translations") or {}
    if not translations:
        return None
    return max(translations, key=lambda k: translations[k].get("created_at") or "")


def add_translation(
    job: dict[str, Any], language: str, texts: list[str], *, engine: str | None, source: str | None = None
) -> str:
    """Store (or replace) the version for ``language``; returns its key. The original is never touched."""
    segments = job.get("segments") or []
    if len(texts) != len(segments):
        msg = f"Translation has {len(texts)} lines for {len(segments)} segments"
        raise speakers.SpeakerError(msg)
    translations = job.setdefault("translations", {})
    existing = next((k for k in translations if k.casefold() == language.strip().casefold()), None)
    key = existing or language.strip()
    translations[key] = {"texts": list(texts), "engine": engine, "source": source, "created_at": now_iso()}
    job["translated_to"] = latest_language(job)
    return key


def remove_translation(job: dict[str, Any], language: str) -> None:
    key = find_language(job, language)
    if key is None:
        msg = "The original text cannot be deleted"
        raise VersionError(msg)
    del job["translations"][key]
    job["translated_to"] = latest_language(job)


# ------------------------------------------------------------------------------ diarizations


def get_diarization(job: dict[str, Any], dia_id: str | None = None) -> dict[str, Any] | None:
    """The version with ``dia_id`` (default: the active one); ``None`` when the job has no diarization at all."""
    versions = job.get("diarizations") or []
    wanted = dia_id or job.get("active_diarization")
    if wanted is None:
        if dia_id:
            msg = f"No diarization {dia_id!r}"
            raise VersionError(msg)
        return None
    found = next((d for d in versions if d["id"] == wanted), None)
    if found is None:
        msg = f"No diarization {wanted!r}"
        raise VersionError(msg)
    return found


def label_segments(segments: list[Segment], turns: Turns) -> list[str | None]:
    """Speaker id per segment from the diarizer's turns (core's midpoint matching)."""
    from vtt_transcribe.diarization_common import SpeakerLabeler

    transcript = speakers.to_result([{**s, "speaker": None} for s in segments])
    labelled = SpeakerLabeler().apply_speakers_to_transcript(transcript, turns)
    parsed = speakers.parse_transcript(labelled)
    if len(parsed) != len(segments):  # cannot line up: leave unlabelled rather than guess
        return [None] * len(segments)
    return [p.get("speaker") for p in parsed]


def _label_for(labels: list[str | None], requested: int | None) -> str:
    count = len({lab for lab in labels if lab})
    if requested:
        return f"{requested} speakers" if requested != 1 else "1 speaker"
    return f"Auto ({count} found)"


def add_diarization(
    job: dict[str, Any],
    turns: Turns,
    num_speakers: int | None,
    *,
    labels: list[str | None] | None = None,
    method: str = "sherpa-onnx",
    make_active: bool = True,
) -> dict[str, Any]:
    """Add a diarization version (never replaces one) and, by default, make it the active one."""
    segments = job.get("segments") or []
    resolved = labels if labels is not None else label_segments(segments, turns)
    seq = int(job.get("dia_seq") or len(job.get("diarizations") or [])) + 1
    job["dia_seq"] = seq
    version = {
        "id": f"d{seq}",
        "label": _label_for(resolved, num_speakers),
        "num_speakers": num_speakers,
        "method": method,
        "created_at": now_iso(),
        "turns": [list(t) for t in turns],
        "labels": list(resolved),
        "names": {},
    }
    job.setdefault("diarizations", []).append(version)
    if make_active:
        job["active_diarization"] = version["id"]
    refresh_result(job)
    return version


def set_active(job: dict[str, Any], dia_id: str) -> None:
    get_diarization(job, dia_id)
    job["active_diarization"] = dia_id
    refresh_result(job)


def remove_diarization(job: dict[str, Any], dia_id: str) -> None:
    versions = job.get("diarizations") or []
    get_diarization(job, dia_id)
    remaining = [d for d in versions if d["id"] != dia_id]
    job["diarizations"] = remaining
    if job.get("active_diarization") == dia_id:
        job["active_diarization"] = remaining[-1]["id"] if remaining else None
    refresh_result(job)


def rename_diarization(job: dict[str, Any], dia_id: str, label: str) -> None:
    version = get_diarization(job, dia_id)
    cleaned = label.strip()
    if not cleaned:
        msg = "A version needs a name"
        raise VersionError(msg)
    assert version is not None
    version["label"] = cleaned[:80]


# ------------------------------------------------------------------------------ views and renderings


def with_speakers(job: dict[str, Any], dia_id: str | None = None) -> tuple[list[Segment], dict[str, str]]:
    """Original segments carrying the chosen (default: active) diarization's speaker labels, plus its names."""
    base = job.get("segments") or []
    version = get_diarization(job, dia_id)
    if version is None:
        return [{**s, "speaker": None} for s in base], {}
    return [{**s, "speaker": lab} for s, lab in zip(base, version["labels"], strict=True)], dict(version.get("names") or {})


def commit_edit(job: dict[str, Any], segments: list[Segment], names: dict[str, str]) -> None:
    """Write an edited (merged / split / renamed) result back to the ACTIVE diarization only."""
    version = get_diarization(job)
    if version is None:
        msg = "This job has no diarization to edit"
        raise VersionError(msg)
    version["labels"] = [s.get("speaker") for s in segments]
    version["names"] = names
    refresh_result(job)


def materialize(
    job: dict[str, Any], *, language: str | None = None, dia_id: str | None = None
) -> tuple[list[Segment], dict[str, str]]:
    """Segments for one (language, diarization) pair: ``text`` original, ``translation`` for ``language`` when given."""
    segments, names = with_speakers(job, dia_id)
    key = find_language(job, language)
    if key is not None:
        texts = job["translations"][key]["texts"]
        segments = [{**s, "translation": t} for s, t in zip(segments, texts, strict=True)]
    return segments, names


def refresh_result(job: dict[str, Any]) -> None:
    """``result``: the original text with the active diarization's names (never a translation)."""
    segments, names = with_speakers(job)
    job["result"] = speakers.to_result(speakers.labelled(segments, names))


def summary(job: dict[str, Any]) -> dict[str, Any]:
    """What exists for a job: the original, every translation, every diarization (for the UI's version pickers)."""
    segments = job.get("segments") or []
    return {
        "job_id": job["job_id"],
        "original": {"language": job.get("detected_language"), "segments": len(segments)},
        "translations": [
            {"language": k, "engine": v.get("engine"), "source": v.get("source"), "created_at": v.get("created_at")}
            for k, v in (job.get("translations") or {}).items()
        ],
        "diarizations": [
            {
                "id": d["id"],
                "label": d["label"],
                "num_speakers": d.get("num_speakers"),
                "method": d.get("method"),
                "created_at": d.get("created_at"),
                "speakers": len({lab for lab in d["labels"] if lab}),
                "active": d["id"] == job.get("active_diarization"),
            }
            for d in (job.get("diarizations") or [])
        ],
        "active_diarization": job.get("active_diarization"),
    }


# ------------------------------------------------------------------------------ bilingual export


def bilingual(segments: list[Segment], names: dict[str, str], fmt: str) -> str:
    """Original and translation together: two lines per segment (a bilingual cue in srt/vtt)."""
    labelled = speakers.labelled(segments, names)
    if fmt == "json":
        import json

        return json.dumps(
            {
                "segments": [
                    {
                        "start": s["start"],
                        "end": s["end"],
                        "speaker": s.get("speaker"),
                        "text": s["text"],
                        "translation": s.get("translation"),
                    }
                    for s in labelled
                ]
            },
            ensure_ascii=False,
            indent=2,
        )

    def pair(s: Segment) -> tuple[str, str]:
        prefix = f"{s['speaker']}: " if s.get("speaker") else ""
        return f"{prefix}{' '.join(s['text'].split())}", " ".join((s.get("translation") or "").split())

    if fmt == "txt":
        return "\n".join(f"{a}\n{b}\n" if b else f"{a}\n" for a, b in map(pair, labelled)).rstrip("\n")
    cues = []
    for i, s in enumerate(labelled, 1):
        first, second = pair(s)
        body = first if not second else f"{first}\n{second}"
        if fmt == "srt":
            cues.append(f"{i}\n{speakers._clock(s['start'], ',')} --> {speakers._clock(s['end'], ',')}\n{body}\n")  # noqa: SLF001
        else:
            cues.append(f"{i}\n{speakers._clock(s['start'])} --> {speakers._clock(s['end'])}\n{body}\n")  # noqa: SLF001
    return ("WEBVTT\n\n" if fmt == "vtt" else "") + "\n".join(cues)


def render(
    job: dict[str, Any], fmt: str, *, language: str | None = None, dia_id: str | None = None, both: bool = False
) -> str:
    """One export: ``language`` None/'original' for the spoken text, else that translation; ``both`` for bilingual."""
    segments, names = materialize(job, language=language, dia_id=dia_id)
    key = find_language(job, language)
    if both:
        if key is None:
            msg = "A bilingual export needs a language"
            raise VersionError(msg)
        return bilingual(segments, names, fmt)
    shown = speakers.labelled(segments, names, translated=key is not None)
    if fmt == "json":
        import json

        return json.dumps({"segments": shown}, ensure_ascii=False, indent=2)
    return {"txt": speakers.to_txt, "vtt": speakers.to_vtt, "srt": speakers.to_srt}[fmt](shown)


# ------------------------------------------------------------------------------ migration


def migrate(job: dict[str, Any]) -> bool:
    """Bring a job saved by an earlier version (speaker, translation and names inside each segment) to the versions
    model. Returns whether anything changed. Idempotent, and lossless: the old translation becomes one language entry and
    the old speaker state becomes diarization ``d1``.
    """
    if job.get("schema") == SCHEMA or not job.get("segments"):
        return False
    old = job["segments"]
    base = base_segments(old)
    labels = [s.get("speaker") for s in old]
    translated_to = job.get("translated_to")
    texts = [s.get("translation") for s in old]
    job["segments"] = base
    job["translations"] = {}
    if translated_to and any(texts):
        job["translations"][translated_to] = {
            "texts": [t or "" for t in texts],
            "engine": job.get("engine"),
            "source": job.get("detected_language"),
            "created_at": job.get("completed_at") or now_iso(),
        }
    job["diarizations"] = []
    job["active_diarization"] = None
    if any(labels):
        job["dia_seq"] = 1
        count = len({lab for lab in labels if lab})
        job["diarizations"].append(
            {
                "id": "d1",
                "label": f"{job['num_speakers']} speakers" if job.get("num_speakers") else f"Auto ({count} found)",
                "num_speakers": job.get("num_speakers"),
                "method": "sherpa-onnx",
                "created_at": job.get("completed_at") or now_iso(),
                "turns": [],  # not kept by earlier versions; the labels below are the record
                "labels": labels,
                "names": dict(job.get("speaker_names") or {}),
            }
        )
        job["active_diarization"] = "d1"
    job["translated_to"] = latest_language(job)
    job.pop("speaker_names", None)
    job["schema"] = SCHEMA
    refresh_result(job)
    return True
