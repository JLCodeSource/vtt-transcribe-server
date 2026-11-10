# Job Persistence Integration

## Current Status

The user management system includes a `TranscriptionJob` model for storing job history and transcripts in the database. However, the actual transcription workflow (in `vtt_server/routes/transcription.py`) currently only tracks jobs in-memory and does not persist them to the database.

## What's Implemented

✅ **Database Schema**
- `TranscriptionJob` model with all required fields
- Foreign key relationship to `User` table
- Status tracking: pending, processing, completed, failed
- Metadata: detected_language, translated_to, with_diarization
- Timestamps: created_at, completed_at

✅ **API Endpoints**
- `GET /job-history/` - List user's jobs with filtering and pagination
- `GET /job-history/{job_id}` - Get job details including transcript
- `DELETE /job-history/{job_id}` - Delete job from history
- `GET /job-history/stats/summary` - Get user statistics

## What's Missing

❌ **Job Creation During Transcription**
Currently, when a user transcribes a file via `/transcribe` or WebSocket, no `TranscriptionJob` record is created in the database.

❌ **Status Updates**
As the transcription progresses (pending → processing → completed/failed), the job status is not updated in the database.

❌ **Transcript Storage**
When transcription completes, the transcript is not saved to the `TranscriptionJob.transcript` field.

## Required Integration Steps

### 1. Create Job on Transcription Start

**File**: `vtt_server/routes/transcription.py`

**Location**: In `transcribe_file()` endpoint after generating `job_id`

```python
# After: job_id = str(uuid.uuid4())

# Create database record
if current_user:  # Only if user is authenticated
    job = TranscriptionJob(
        job_id=job_id,
        user_id=current_user.id,
        filename=file.filename,
        status="pending",
        detected_language=target_language if target_language != "en" else None,
        translated_to=translate_to if translate_to else None,
        with_diarization=diarize,
    )
    db.add(job)
    await db.commit()
```

### 2. Update Status to "processing"

**File**: `vtt_server/routes/transcription.py`

**Location**: In background transcription task before calling `VideoTranscriber.transcribe()`

```python
# Update job status to processing
if current_user:
    result = await db.execute(
        select(TranscriptionJob).where(TranscriptionJob.job_id == job_id)
    )
    job = result.scalar_one_or_none()
    if job:
        job.status = "processing"
        await db.commit()
```

### 3. Store Transcript on Completion

**File**: `vtt_server/routes/transcription.py`

**Location**: In background transcription task after successful transcription

```python
# After transcript is generated
if current_user:
    result = await db.execute(
        select(TranscriptionJob).where(TranscriptionJob.job_id == job_id)
    )
    job = result.scalar_one_or_none()
    if job:
        job.status = "completed"
        job.transcript = transcript
        job.completed_at = datetime.now(timezone.utc)
        await db.commit()
```

### 4. Handle Errors

**File**: `vtt_server/routes/transcription.py`

**Location**: In background transcription task exception handlers

```python
# In exception handler
if current_user:
    result = await db.execute(
        select(TranscriptionJob).where(TranscriptionJob.job_id == job_id)
    )
    job = result.scalar_one_or_none()
    if job:
        job.status = "failed"
        job.error = str(e)
        job.completed_at = datetime.now(timezone.utc)
        await db.commit()
```

### 5. WebSocket Integration

Similar changes needed in `vtt_server/routes/websockets.py` for the WebSocket transcription workflow.

## Testing Requirements

Once job persistence is implemented, add integration tests:

1. **Test job creation**: Verify job record is created when transcription starts
2. **Test status progression**: Verify status changes from pending → processing → completed
3. **Test transcript storage**: Verify transcript is saved on completion
4. **Test error handling**: Verify job status is set to "failed" on errors
5. **Test user isolation**: Verify users can only see their own jobs

## Benefits of Full Integration

- 📊 **Complete audit trail** of all transcription operations
- 🔍 **Searchable transcript history** for users
- 📈 **Usage analytics** and billing data
- 🔄 **Resume capability** for failed jobs
- 🎯 **User-specific transcription limits** enforcement

## Notes

- Integration should be **backward compatible** for unauthenticated transcription requests
- Consider making job persistence **optional** via configuration flag
- May want to add **job retention policies** (auto-delete old jobs)
- Consider adding **job priority** and **queue management** features

## Timeline

This integration is planned for a future milestone after the core user management system has been validated in production.
