from flask import Flask, request, jsonify, Response
import os
import datetime
import whisper
import psycopg2
from celery import Celery
#from celery.result import AsyncResult
from dotenv import load_dotenv
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from shared.celery_config import app  # Import the Celery app
from kombu import Queue
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
import time

# Load .env file
load_dotenv(dotenv_path=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "vars.env")))

app = Flask(__name__)

# Metrics definition
STT_REQUEST_COUNT = Counter("stt_request_count", "Total de requisições recebidas")
STT_LATENCY = Histogram("stt_latency_seconds", "Tempo de processamento da transcrição")
STT_AUDIO_SIZE = Histogram("stt_audio_size_bytes", "Tamanho do arquivo de áudio recebido")


# Fetch database connection from environment variables
db_config = {
    "dbname": os.getenv("POSTGRES_DB", "stt_service"),
    "user": os.getenv("POSTGRES_USER", "postgres"),
    "password": os.getenv("POSTGRES_PASSWORD", "mypassword"),
    "host": os.getenv("POSTGRES_HOST", "postgres_stt"),
    "port": int(os.getenv("POSTGRES_PORT", 5432))
}

app = Flask(__name__)


# Configure Celery
app.config['CELERY_BROKER_URL'] = 'redis://redis:6379/0'  # Redis URL for Celery broker
app.config['CELERY_RESULT_BACKEND'] = 'redis://redis:6379/0'  # Redis for result backend


# Define the default queue and custom queues
task_queues = (
    Queue('stt', routing_key='stt'),  # All tasks with stt.# go to stt
    Queue('llm', routing_key='llm'),  # All tasks with llm.# go to llm
    Queue('tts', routing_key='tts'),  # All tasks with llm.# go to llm
)

# Define task routes
task_routes = {
    'transcribe_audio': {'queue': 'stt', 'routing_key': 'stt'},
    'process_llm_query': {'queue': 'llm', 'routing_key': 'llm'},
    'generate_tts_audio': {'queue': 'tts', 'routing_key': 'tts'},
}

celery = Celery(
    "services.stt_api",
    broker=app.config['CELERY_BROKER_URL'],
    backend=app.config['CELERY_RESULT_BACKEND']  # Use backend instead of result_backend
)
celery.conf.update(
    task_queues=task_queues,
    task_routes=task_routes,
    broker_connection_retry_on_startup=True,  # Ensures retries on broker connection
)


# Celery Task to handle STT transcription
@celery.task(bind=True, name="transcribe_audio")
def transcribe_audio(self, audio_path, audio_filename):

    try:
        # Request metrics
        STT_REQUEST_COUNT.inc()
        start_time = time.time()
        print(f"STT_REQUEST_COUNT: {STT_REQUEST_COUNT}")

        print(f"Transcribing audio file: {audio_filename}")
        
        # Audio size metrics
        with open(audio_path, "rb") as audio_file:
            audio_size = len(audio_file.read())
            STT_AUDIO_SIZE.observe(audio_size)
            print(f"Audio size metrics: {audio_size}")
            print(f"STT_AUDIO_SIZE: {STT_AUDIO_SIZE}")

        model = whisper.load_model("base", device="cpu")
        result = model.transcribe(audio_path, fp16=False)
        transcription = result["text"]

        # Latency metrics
        duration = time.time() - start_time
        STT_LATENCY.observe(duration)
        print(f"Latency Metrics: {duration}")
        print(f"STT_LATENCY: {STT_LATENCY}")

        # Save transcription and audio to PostgreSQL
        with open(audio_path, "rb") as audio_file:
            audio_binary = audio_file.read()
        
        try:
            # Establish connection to PostgreSQL
            conn = psycopg2.connect(**db_config)
            cursor = conn.cursor()
            
            # Check if the table exists
            print("🔍 Checking if table exists...")
            cursor.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'stt_transcriptions';")
            table_exists = cursor.fetchone()[0]
            
            if table_exists:
                print("✅ Table 'stt_transcriptions' exists.")
            else:
                print("❌ Table 'stt_transcriptions' does NOT exist! Creating it now...")
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS stt_transcriptions (
                        id SERIAL PRIMARY KEY,
                        audio_filename TEXT NOT NULL,
                        transcription TEXT NOT NULL,
                        audio_data BYTEA NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                print("✅ Table 'stt_transcriptions' created!")
            
            # Insert the data
            cursor.execute(
                """
                INSERT INTO stt_transcriptions (audio_filename, transcription, audio_data)
                VALUES (%s, %s, %s)
                RETURNING id;
                """,
                (audio_filename, transcription, psycopg2.Binary(audio_binary))
            )
            
            inserted_id = cursor.fetchone()[0]  # Get the inserted row ID
            conn.commit()
            print(f"✅ Data inserted successfully! Inserted ID: {inserted_id}")
            
            # Verify by selecting last 5 entries
            cursor.execute("SELECT id, audio_filename, transcription FROM stt_transcriptions ORDER BY created_at DESC LIMIT 5;")
            rows = cursor.fetchall()
            
            print("📌 Last 5 records in stt_transcriptions:")
            for row in rows:
                print(row)
        
        except Exception as e:
            print(f"❌ ERROR: {e}")
        
        finally:
            cursor.close()
            conn.close()
            print("🔍 Connection closed.")

        return transcription

    except Exception as e:
        raise self.retry(exc=e)
    
@app.route("/metrics")
def metrics():
    print(CONTENT_TYPE_LATEST)
    #return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)

@app.route('/stt', methods=['POST'])
def stt_api():
    try:
        # Get the uploaded audio file
        file = request.files['audio']
        audio_filename = file.filename
        audio_path = os.path.join(os.path.dirname(__file__), f"{audio_filename}")

        # Save the uploaded file temporarily
        file.save(audio_path)

        # Call the Celery task to process the audio
        #task = transcribe_audio.delay(audio_path, audio_filename)
        task = transcribe_audio.apply_async(args=[audio_path, audio_filename], routing_key='stt')


        # Return only the task ID so the client can track the task's progress
        return jsonify({"message": "Task started", "task_id": task.id})

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    

@app.route('/task_status_stt/<task_id>', methods=['GET'])
def get_task_status(task_id):
    try:
        task = celery.AsyncResult(task_id)
        print(task)

        if task.state == 'PENDING':
            response = {"status": "PENDING", "result": "Task still pending"}
        elif task.state == 'SUCCESS':
            response = {"status": "SUCCESS", "result": task.result}
        elif task.state == 'FAILURE':
            response = {"status": "FAILURE", "result": str(task.result)}
        else:
            response = {"status": task.state, "result": None}

        return jsonify(response)
    except Exception as e:
        return jsonify({"error": str(e)}), 500



if __name__ == "__main__":
#    app.run(host="localhost", port=8502, debug=True)
    app.run(host="0.0.0.0", port=8502, debug=True)
