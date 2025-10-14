from celery import Celery

# Define Celery instance
app = Celery(
    "celery_worker",
    broker="redis://redis:6379/0",
    backend="redis://redis:6379/0"
)

# Autodiscover tasks from STT, LLM, and TTS
app.autodiscover_tasks([
    "services.stt_api",
    "services.llm_api",
    "services.tts_api"
])

if __name__ == "__main__":
    app.start()
